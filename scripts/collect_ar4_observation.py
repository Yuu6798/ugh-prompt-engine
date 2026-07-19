"""scripts/collect_ar4_observation.py — AR4 実観測バッチ orchestration (MusicGen).

**手動 runbook。CI では絶対に実行しない**（`scripts/collect_musicgen_takes.py` と同じ
DD-A 規律: torch 経由の非決定論的推論を行うため、`pip install -e ".[musicgen]"` が
必要）。目的は AR4（生成後観測）の observation report を初めて実生成物で埋めること
（Design Memo:
`design_memo_ar4_musicgen.md`、`docs/arrangement_identity_planning.md` §5）。

新規ロジックは「package が compile した prompt テキストを読む -> n take 生成 ->
sha256/タイムスタンプを記録する」というオーケストレーションのみに限定する。
モデルロード/生成そのものは `scripts/collect_musicgen_takes.py` の
`_import_musicgen_stack`（torch/transformers の遅延 import ガード）と
`_sha256_file` を再利用し、生成ループの形（`processor(...)` -> `model.generate(...)`
-> `wavfile.write`）も同スクリプトの `perform_takes` と同型に揃える —
違いは prompt の出所のみ（`perform_takes` はスコアから
`ExternalPromptAdapter` で再導出するが、本スクリプトは既にコンパイル済みの
`performance_package.json` の `prompt.text` を検証済みチェーンの終端としてそのまま
読む。chain の正直さ: 楽譜 -> package -> 生成器）。

Usage:
    # 1) 生成（手動・torch 必須。canonical n=2 バッチ）
    python scripts/collect_ar4_observation.py generate \\
        --package /path/to/performance_package.json \\
        --output-dir /tmp/ar4_takes \\
        --manifest-out /tmp/ar4_takes/ar4_takes_manifest.json \\
        --model-revision 4c8334b02c6ec4e8664a91979669a501ec497792

    # 2) Phase 3 決定論スポット検証: 上と同一 --package/--model-revision で
    #    --take-index を 1 本ずつ指定し、別プロセスで再生成する
    python scripts/collect_ar4_observation.py generate \\
        --package /path/to/performance_package.json \\
        --output-dir /tmp/ar4_spotcheck \\
        --manifest-out /tmp/ar4_spotcheck/take0_manifest.json \\
        --take-index 0 --model-revision 4c8334b02c6ec4e8664a91979669a501ec497792
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_musicgen_takes import _import_musicgen_stack, _sha256_file  # noqa: E402
from svp_rpe.arrange.contract import build_preservation_contract  # noqa: E402
from svp_rpe.arrange.identity import IdentityManifest  # noqa: E402
from svp_rpe.arrange.models import ArrangementSpec  # noqa: E402
from svp_rpe.arrange.package import (  # noqa: E402
    compute_derived_score_sha256,
    compute_device_profile_sha256,
    compute_preservation_contract_sha256,
)
from svp_rpe.arrange.resolver import resolve_arrangement  # noqa: E402
from svp_rpe.compose.device_profile import load_device_profile  # noqa: E402
from svp_rpe.compose.models import CompositionScore  # noqa: E402
from svp_rpe.compose.prompt_renderer import resolve_backend_descriptor  # noqa: E402

SCHEMA_VERSION = "1.0"
GENERATOR = "collect_ar4_observation.py"
DEFAULT_FIXTURE_ID = "ar4_musicgen_midnight_signal_edm"
DEFAULT_MODEL_ID = "facebook/musicgen-small"
DEFAULT_SEED_BASE = 8000  # AR4 専用レンジ（sample_seed の 1000+ / perform_takes の
# 既定 seed_base=5000 と衝突しない。ar4_plan.yaml の seeds.formula と同一定義）

# `performance_package` provenance recording 用の既定入力（repo 相対）。
# ar4_plan.yaml#scope / #prompt_source.compiled_via と同一の値 — `--package` が
# 指す performance_package.json 自体は scratch ビルド成果物でコミット対象外だが、
# これらの入力からの再現レシピは repo 相対パスなのでマシン非依存に記録できる
# （Codex P2 review #191: `str(package_path)` は絶対パスでマシン固有だった）。
DEFAULT_SCORE = ROOT / "examples/arrangement/midnight_signal/composition_score.yaml"
DEFAULT_IDENTITY_MANIFEST = ROOT / "examples/arrangement/midnight_signal/identity_manifest.yaml"
DEFAULT_ARRANGEMENT = (
    ROOT / "examples/arrangement/midnight_signal/edm.identity.musicgen.arrangement.yaml"
)
DEFAULT_CAPABILITY_PROFILE = ROOT / "config/capability_profiles/musicgen.yaml"


def _utc_now_iso() -> str:
    """実測 UTC タイムスタンプ（秒精度、`date -u` 相当）。推定値は使わない。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_package_prompt(package_path: Path) -> tuple[str, str, dict[str, Any]]:
    """コンパイル済み `performance_package.json` の `prompt.text` を検証済みの
    終端としてそのまま読む（chain の正直さ: 再導出しない）。

    `prompt` が `None`、または `text` が空/空白のみの場合は ``ValueError`` を送出
    する — 呼び出し側はここで停止して報告し、プロンプトを発明してはならない
    （Design Memo Phase 0 #3 / 呼び出し元指示）。

    戻り値の 3 要素目は生の JSON dict（`data`）— `build_package_provenance` が
    `inputs.*.sha256` pin を突合するのに使う（Codex 3R P2 review #191,
    discussion_r3610153978: 誤指定/実在しないレシピ入力パスを package の pin で
    検出するため、この呼び出し側で一度読んだ JSON をそのまま渡し、二重読みしない）。
    """
    package_bytes = package_path.read_bytes()
    package_sha256 = hashlib.sha256(package_bytes).hexdigest()
    data = json.loads(package_bytes)
    prompt = data.get("prompt")
    if prompt is None:
        raise ValueError(
            f"performance_package.json at {package_path} has prompt=None — the "
            "style_prompt channel was omitted at compile time (see 'warnings' in "
            "the sibling compilation_report.json). Refusing to invent a prompt; "
            "stop here and report."
        )
    text = prompt.get("text")
    if text is None or not str(text).strip():
        raise ValueError(
            f"performance_package.json at {package_path} has an empty prompt.text. "
            "Refusing to invent a prompt; stop here and report."
        )
    return str(text), package_sha256, data


def generate_ar4_takes(
    prompt_text: str,
    *,
    output_dir: Path,
    take_indices: list[int],
    seed_base: int = DEFAULT_SEED_BASE,
    model_id: str = DEFAULT_MODEL_ID,
    model_revision: Optional[str] = None,
    duration_seconds: float = 12.0,
    guidance_scale: float = 3.0,
) -> dict[str, Any]:
    """`take_indices` 各要素について MusicGen 推論を 1 回ずつ行う（seed = seed_base +
    take_index）。モデルロード/生成そのものは
    `scripts/collect_musicgen_takes.py` の `_import_musicgen_stack` を再利用し、
    生成ループの形も同スクリプトの `perform_takes` と同型に揃える。

    音声は ``output_dir`` に WAV として書き出すのみでコミット対象にはしない
    （DD-A）。各生成コールの実測 UTC 開始/終了時刻（秒精度・推定禁止）を
    ``timestamps`` として返す — 呼び出し側が
    ``ar4_generation_timestamps.yaml`` を書き出すのに使う。
    """
    torch, AutoProcessor, MusicgenForConditionalGeneration = _import_musicgen_stack()
    from scipy.io import wavfile

    processor = AutoProcessor.from_pretrained(model_id, revision=model_revision)
    model = MusicgenForConditionalGeneration.from_pretrained(model_id, revision=model_revision)
    model.eval()
    sampling_rate = int(model.config.audio_encoder.sampling_rate)
    resolved_revision = getattr(model.config, "_commit_hash", None) or model_revision

    output_dir.mkdir(parents=True, exist_ok=True)
    max_new_tokens = int(duration_seconds * 50)

    samples: list[dict[str, Any]] = []
    timestamps: list[dict[str, Any]] = []
    for take_index in take_indices:
        seed = seed_base + take_index
        sample_id = f"take{take_index}"
        started_at = _utc_now_iso()
        torch.manual_seed(seed)
        inputs = processor(text=[prompt_text], padding=True, return_tensors="pt")
        audio_values = model.generate(
            **inputs,
            do_sample=True,
            guidance_scale=guidance_scale,
            max_new_tokens=max_new_tokens,
        )
        waveform = audio_values[0, 0].detach().cpu().numpy()
        ended_at = _utc_now_iso()

        audio_path = output_dir / f"{sample_id}.wav"
        wavfile.write(str(audio_path), sampling_rate, waveform)
        audio_sha256 = _sha256_file(audio_path)

        samples.append(
            {
                "sample_id": sample_id,
                "take_index": take_index,
                "seed": seed,
                "audio_path": audio_path.name,
                "audio_sha256": audio_sha256,
                "duration_seconds": duration_seconds,
                "guidance_scale": guidance_scale,
                "model_id": model_id,
                "model_revision": resolved_revision,
            }
        )
        timestamps.append(
            {
                "sample_id": sample_id,
                "take_index": take_index,
                "seed": seed,
                "started_at_utc": started_at,
                "ended_at_utc": ended_at,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "prompt": prompt_text,
        "model_id": model_id,
        "model_revision": resolved_revision,
        "duration_seconds": duration_seconds,
        "guidance_scale": guidance_scale,
        "samples": samples,
        "timestamps": timestamps,
    }


def _repo_relative(path: Path) -> Optional[str]:
    """`path` を repo-root 相対の POSIX パスへ変換する。

    リポジトリ外のパス（scratch ビルドディレクトリ等）は ``None`` を返す —
    マシン固有の絶対パスへフォールバックしない（D-4: 値の捏造をしない代わりに
    欠落を許容する）。
    """
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None


def _display_path_for_reason(path: Path) -> str:
    """検証失敗理由（`performance_package.note` に直列化されうる）に埋め込む
    人間可読なパス表現。``str(path)`` をそのまま使うとマシン固有の絶対パスが
    committed provenance artifact に焼き込まれてしまう（Codex 9R P2 review
    #191, discussion_r3610299285）ため、ここで正規化する:

    - リポジトリ配下なら repo-root 相対の POSIX パス（``_repo_relative`` と
      同じ変換）を返す。
    - リポジトリ外（誤指定パス等）なら、絶対パスを漏らさずファイル名のみを
      返す — 「存在しない」ことだけを報告できれば十分で、マシン固有の
      ディレクトリ構造を note に残す必要はない。
    """
    relative = _repo_relative(path)
    if relative is not None:
        return relative
    return path.name


def _package_input_pin(package_data: dict[str, Any], input_name: str) -> Optional[str]:
    """`performance_package.json` の `inputs.<input_name>.sha256` pin を取得する。

    `PackageInputs`（`src/svp_rpe/arrange/package.py`）のうち
    ``identity_manifest`` と ``capability_profile`` は compile 時に読んだ raw
    ファイル bytes を直接 sha256 pin している（`compile_performance_package` の
    `hashlib.sha256(manifest_bytes)` / `hashlib.sha256(profile_bytes)`）ので、
    ここで raw ファイルを再ハッシュして突合できる。欠落/型不正はすべて ``None``
    （fail-closed の起点 — 呼び出し側は pin なしとして existence-only 検証に
    落ちる。例外は投げない）。
    """
    inputs = package_data.get("inputs")
    if not isinstance(inputs, dict):
        return None
    entry = inputs.get(input_name)
    if not isinstance(entry, dict):
        return None
    sha256 = entry.get("sha256")
    if isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256):
        return sha256
    return None


def _artifact_base_locators(package_data: dict[str, Any]) -> list[str]:
    """`channel_artifacts[*][*].artifact_base.locator` の distinct 値一覧
    （安定した順序のためソート済み）。

    `compile_performance_package`（`src/svp_rpe/arrange/package.py`）は
    channel_artifacts を持つ package を compile する際、
    ``os.path.relpath(identity_manifest の親ディレクトリ, --output-dir)``
    を ``artifact_base.locator`` として package JSON に埋め込む。つまり
    `build_recipe.compile_command` の ``--output-dir <output-dir>``
    プレースホルダは実は自由な値ではなく、この relpath 計算結果が locator
    と一致する geometry でなければ recompile 結果の
    `performance_package.sha256` が pin と一致しない（Codex 7R P2 review
    #191, discussion_r3610250841）。

    channel_artifacts が空（全 anchor が unsupported/unknown 等で
    delivered_channel を持たない）の package では locator がどこにも
    現れず、この関数は空リストを返す — その場合 package sha256 は
    ``--output-dir`` の実際の値に依存しない（幾何非依存）。
    """
    locators: set[str] = set()
    channel_artifacts = package_data.get("channel_artifacts")
    if not isinstance(channel_artifacts, dict):
        return []
    for references in channel_artifacts.values():
        if not isinstance(references, list):
            continue
        for reference in references:
            if not isinstance(reference, dict):
                continue
            artifact_base = reference.get("artifact_base")
            if not isinstance(artifact_base, dict):
                continue
            locator = artifact_base.get("locator")
            if isinstance(locator, str):
                locators.add(locator)
    return sorted(locators)


def _verify_recipe_input(
    path: Path, *, pin_sha256: Optional[str]
) -> tuple[Optional[bytes], Optional[str]]:
    """単一レシピ入力を検証し、読めた場合は raw bytes も返す（Codex 6R P2 review
    #191: `build_recipe.inputs.<name>` に検証済み per-input sha256 を焼き込む
    ため、この検証パスで読んだ bytes をそのまま呼び出し側の recipe sha256
    記録に再利用する — 単一 read 規律。検証と hash 記録で二重読みしない）。

    戻り値は ``(raw_bytes, failure_reason)``: 問題なければ
    ``(file_bytes, None)``、ファイルが存在しない場合は ``(None, 理由文字列)``、
    存在するが pin 不一致の場合は ``(file_bytes, 理由文字列)``（bytes は
    読めているので呼び出し側は失敗時でも捨てずに保持してよい）。

    検証内容（Codex 3R P2 review #191, discussion_r3610153978: 誤指定/実在
    しないパスでも repo-relative に解決できさえすれば `build_recipe` を出して
    しまっていた欠陥への修正）:

    (a) 実在する regular file であること
    (b) ``pin_sha256`` が与えられている場合（package がこの入力種を raw bytes で
        pin している場合）、ファイル実 bytes の sha256 が pin と一致すること

    ``score`` / ``arrangement`` には raw bytes 相当の pin が
    `performance_package.json` 内に存在しない（``derived_score`` は
    `resolve_arrangement` 後の再レンダリングであり raw な score.yaml の bytes
    ではなく、``preservation_contract`` の pin は manifest+spec から合成した
    契約 JSON の hash であり arrangement spec の raw bytes ではない）ため、この
    2 種は呼び出し側が ``pin_sha256=None`` を渡し (a) のみで検証される —
    その代わり、この 2 種が実際に正しい組み合わせかどうかは
    `_recompute_derived_score_sha256` / `_recompute_preservation_contract_sha256`
    が幾何非依存の合成 pin を再計算して別途突合する（Codex 4R P2 review #191,
    discussion_r3610170990: existence-only では実在する別の YAML への誤指定を
    検出できなかった欠陥への修正）。この 2 種についても raw bytes は返す —
    (a) を通った時点でファイルは読めているので、`build_recipe.inputs.<name>`
    の sha256 記録にそのまま再利用できる。
    """
    if not path.is_file():
        return None, f"{_display_path_for_reason(path)} does not exist or is not a regular file"
    raw_bytes = path.read_bytes()
    if pin_sha256 is not None:
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha256 != pin_sha256:
            return raw_bytes, (
                f"{_display_path_for_reason(path)} sha256 {actual_sha256} does not match "
                f"package-pinned sha256 {pin_sha256}"
            )
    return raw_bytes, None


def _load_yaml_mapping(raw_bytes: bytes) -> dict[str, Any]:
    data = yaml.safe_load(raw_bytes)
    if not isinstance(data, dict):
        raise ValueError("expected a YAML mapping")
    return data


def _recompute_derived_score_sha256(
    score_bytes: bytes, arrangement_bytes: bytes
) -> Optional[str]:
    """``--score`` / ``--arrangement`` の raw bytes から
    `inputs.derived_score.sha256` pin を再計算する（Codex 4R P2 review #191,
    discussion_r3610170990）。

    引数は raw bytes を受け取る（``Path`` ではない）— 呼び出し側
    (`build_package_provenance`) が ``_verify_recipe_input`` で既に読んだ bytes
    をそのまま渡す単一 read 規律（Codex 6R P2 review #191: 検証と
    `build_recipe.inputs.<name>.sha256` 記録で二重読みしない）。

    `derived_score` pin は score + arrangement spec の内容のみで決まり、
    package の出力ディレクトリ（幾何）には依存しない —
    `compile_performance_package`（`src/svp_rpe/arrange/package.py`）と完全に
    同じ ``resolve_arrangement(source, spec)`` -> `compute_derived_score_sha256`
    の経路を再利用する（ロジックの緩いコピーはしない）。YAML 不正・スキーマ
    不一致・resolve 失敗はすべて ``None`` を返す fail-closed（例外は投げない —
    呼び出し側は pin 突合不能として扱い、検証失敗にフォールバックする）。
    """
    try:
        source = CompositionScore.model_validate(_load_yaml_mapping(score_bytes))
        spec = ArrangementSpec.model_validate(_load_yaml_mapping(arrangement_bytes))
        resolution = resolve_arrangement(source, spec)
        return compute_derived_score_sha256(resolution.derived_score)
    except Exception:
        return None


def _recompute_preservation_contract_sha256(
    identity_manifest_bytes: bytes, arrangement_bytes: bytes
) -> Optional[str]:
    """``--identity-manifest`` / ``--arrangement`` の raw bytes から
    `inputs.preservation_contract.sha256` pin を再計算する（同上の理由。引数が
    raw bytes である理由も同上 — 単一 read 規律）。

    `preservation_contract` pin は identity manifest + arrangement spec の
    内容のみで決まり、package の出力ディレクトリ（幾何）には依存しない —
    `compile_performance_package` と完全に同じ ``build_preservation_contract``
    -> `compute_preservation_contract_sha256` の経路を再利用する。失敗はすべて
    ``None``（fail-closed。例外は投げない）。
    """
    try:
        manifest = IdentityManifest.model_validate(
            _load_yaml_mapping(identity_manifest_bytes)
        )
        spec = ArrangementSpec.model_validate(_load_yaml_mapping(arrangement_bytes))
        manifest_sha256 = hashlib.sha256(identity_manifest_bytes).hexdigest()
        spec_sha256 = hashlib.sha256(arrangement_bytes).hexdigest()
        contract = build_preservation_contract(
            manifest, spec, manifest_sha256=manifest_sha256, spec_sha256=spec_sha256
        )
        return compute_preservation_contract_sha256(contract)
    except Exception:
        return None


def _recompute_device_profile_pin(
    score_bytes: bytes, arrangement_bytes: bytes
) -> Optional[tuple[str, str, Optional[str]]]:
    """``--score`` / ``--arrangement`` の raw bytes から、`svprpe package` が
    auto-load する device profile の (generator, status, sha256) を再計算する
    （Codex 5R P2 review #191, discussion_r3610193512）。引数が raw bytes で
    ある理由は上 2 関数と同じ単一 read 規律（Codex 6R P2 review #191）。

    ここで返す ``sha256`` は device profile の **canonical JSON**（Pydantic
    モデル再シリアライズ後）の pin であり、`performance_package.json` の
    `inputs.device_profile.sha256` と突合するためのもの。`build_recipe` に
    記録する raw ファイル bytes の sha256（他 4 入力と同一の意味論）とは別の
    値なので混同しないこと — 呼び出し側 (`build_package_provenance`) が
    device profile の raw bytes を別途読んで `build_recipe.inputs.device_profile`
    に記録する。

    `compile_performance_package`（`src/svp_rpe/arrange/package.py`）は compile
    時に ``resolve_backend_descriptor(resolution.derived_score.rendering.
    target_backend).profile_key`` を generator として
    `config/device_profiles/<generator>.yaml` を auto-load し（score/arrangement
    には一切現れない、compile command 引数にもならない隠れ入力）、その canonical
    JSON sha256 を `inputs.device_profile` として package に pin embed する。
    device profile を差し替えて（あるいは既定から変更して）compile した package
    に対し、旧 4 入力（score/identity_manifest/arrangement/capability_profile）
    だけを検証しても device profile の差分は検出できず、このレシピに従って
    recompile すると異なる ``performance_package.sha256`` を産む "false
    reproduction recipe" になり得る、という指摘への対応。

    `compile_performance_package` と完全に同じ経路
    （``resolve_arrangement`` -> ``resolve_backend_descriptor`` ->
    `load_device_profile` -> `compute_device_profile_sha256`）を再利用する。

    ``status="not_found"``（generator に対応する device profile ファイルが
    存在しない）は `DeviceProfileInput` 上正当な状態であり、その場合 sha256 は
    ``None`` を返す（device profile 自体が package の必須フィールドである一方、
    その中身が「ファイル無し」であることは正当に起こりうる — package.py の
    `_device_profile_input` 参照）。YAML 不正・スキーマ不一致・resolve 失敗は
    すべて ``None``（fail-closed。呼び出し側は pin 突合不能として扱い、検証失敗
    にフォールバックする）。
    """
    try:
        source = CompositionScore.model_validate(_load_yaml_mapping(score_bytes))
        spec = ArrangementSpec.model_validate(_load_yaml_mapping(arrangement_bytes))
        resolution = resolve_arrangement(source, spec)
        generator = resolve_backend_descriptor(
            resolution.derived_score.rendering.target_backend
        ).profile_key
        device_profile = load_device_profile(generator)
    except Exception:
        return None
    if device_profile is None:
        return (generator, "not_found", None)
    return (generator, "loaded", compute_device_profile_sha256(device_profile))


def build_package_provenance(
    package_path: Path,
    package_sha256: str,
    package_data: Optional[dict[str, Any]] = None,
    *,
    score: Optional[Path] = None,
    identity_manifest: Optional[Path] = None,
    arrangement: Optional[Path] = None,
    capability_profile: Optional[Path] = None,
) -> dict[str, Any]:
    """`performance_package` provenance フィールドを組み立てる（sha256 pin +
    マシン非依存な安定レシピ）。

    ``str(package_path)`` はマシン固有の絶対パスになるため記録しない
    （Codex P2 review #191, discussion_r3610116228）。優先順位:

    1. ``package_path`` 自体がリポジトリ内ファイルなら、その repo-root 相対
       パスをそのまま記録する。
    2. リポジトリ外（AR4 runbook の通常経路である scratch ビルド）の場合、
       ``score``/``identity_manifest``/``arrangement``/``capability_profile``
       が全て与えられ、かつ全て repo 相対に解決でき、かつ以下の 2 段の検証を
       全て通れば、それらの入力パスとコンパイルコマンドからなる構造化レシピ
       （``build_recipe``）を記録する:

       (a) ``_verify_recipe_input`` で 4 入力それぞれの実在を確認する。
           ``identity_manifest`` / ``capability_profile`` は package の
           `inputs.<name>.sha256`（raw bytes pin）とも突合する。この検証で
           読んだ raw bytes はそのまま (b) の再計算と、下記の per-input
           sha256 記録（`build_recipe.inputs.<name>.sha256`）に再利用する
           （単一 read 規律 — 検証と hash 記録で二重読みしない）。
       (b) ``score`` + ``arrangement`` から `inputs.derived_score.sha256`
           を、``identity_manifest`` + ``arrangement`` から
           `inputs.preservation_contract.sha256` を、それぞれ
           `compile_performance_package` と同じ経路
           （`_recompute_derived_score_sha256` /
           `_recompute_preservation_contract_sha256`、内部的に
           `src/svp_rpe/arrange/package.py` の
           `compute_derived_score_sha256` / `compute_preservation_contract_sha256`
           を再利用）で再計算し、package の pin と突合する（Codex 4R P2
           review #191, discussion_r3610170990: (a) だけでは
           existence-only のため、実在する別の YAML への誤指定
           （例: 別 work の score / 別 variant の arrangement）を
           `build_recipe` が誤って裏書きしてしまっていた欠陥への修正 —
           これで 4 入力すべてが package の pin に検証で結ばれる）。
       (c) ``score`` + ``arrangement`` から `svprpe package` が auto-load する
           device profile（`config/device_profiles/<generator>.yaml`、
           compile command 引数にも score/arrangement の中身にも一切現れない
           隠れ入力）の (generator, status, sha256) を
           `_recompute_device_profile_pin` で再計算し、package の
           `inputs.device_profile` と突合する（Codex 5R P2 review #191,
           discussion_r3610193512: (a)(b) の 4 入力がすべて検証を通っても
           device profile だけが差し替わっていれば recompile 結果の
           package sha256 が変わる "false reproduction recipe" になり得た
           欠陥への修正）。一致すれば `build_recipe.inputs.device_profile`
           に auto-loaded 入力として明示的に追記する（status="not_found"、
           すなわち generator に対応する device profile ファイルが存在しない
           場合は正当な状態であり、`inputs` には追記せず
           `auto_loaded_inputs` にその旨を記録するのみ）。

       ``package_data`` が ``None``、または `inputs.derived_score.sha256` /
       `inputs.preservation_contract.sha256` / `inputs.device_profile` pin
       自体が package JSON に存在しない場合は、レシピの正当性を主張できない
       ため検証失敗として扱う（(a) のみを通っても `build_recipe` は emit
       しない）。

       検証を全て通った場合、``build_recipe.inputs.<name>`` は
       ``"<repo相対パス>"`` の単純文字列ではなく
       ``{"path": "<repo相対パス>", "sha256": "<検証時点の raw bytes
       sha256>"}`` オブジェクトとして記録する（Codex 6R P2 review #191:
       repo 相対パスのみでは、後年 checkout で入力ファイルの中身が変わっても
       最終 package sha256 の不一致でしか検知できず、どの入力がドリフトした
       か特定できない欠陥への修正）。5 入力（score / identity_manifest /
       arrangement / capability_profile / device_profile）すべて raw bytes
       の sha256 で一様に統一する — ``device_profile`` は package の
       `inputs.device_profile.sha256` が canonical JSON（Pydantic 再
       シリアライズ後）の pin であるのに対し、`build_recipe` 側は他の 4 入力
       と同じ raw ファイル bytes の sha256（両者は別の値）。

       さらに `build_recipe.output_geometry` として、``--output-dir
       <output-dir>`` プレースホルダが実は自由な値ではないことを明記する
       （Codex 7R P2 review #191, discussion_r3610250841）:
       `compile_performance_package` は channel_artifacts を持つ package を
       compile する際、``os.path.relpath(identity_manifest の親ディレクトリ,
       --output-dir)`` を各 `ChannelArtifactReference.artifact_base.locator`
       として package sha256 に焼き込む。``package_data["channel_artifacts"]``
       から distinct な locator を集め（`_artifact_base_locators`）:

       - locator が 1 件以上あれば、その値（複数 distinct なら配列）と
         「recompile 時は relpath がこの locator に一致する --output-dir を
         選ぶ必要がある」という制約文を記録する。
       - locator がゼロ（channel_artifacts が空。例えば全 anchor が
         unsupported/unknown で delivered_channel を持たない場合）なら、
         フィールドを省略せず「幾何非依存であることを確認済み」の旨を
         明示する（省略すると「未検討」なのか「非依存確認済み」なのか
         将来の読み手が区別できないため）。
    3. 上記いずれも成立しない場合（レシピ入力が揃っていない、または検証に
       失敗した場合）は sha256 のみを provenance として残す（部分的な入力や
       検証未通過の入力から不完全/偽のレシピを捏造しない — 偽レシピより正直な
       欠落）。検証失敗の場合は ``note`` にどの入力がなぜ失敗したかを記録する。
    """
    provenance: dict[str, Any] = {"sha256": package_sha256}

    repo_relative_package = _repo_relative(package_path)
    if repo_relative_package is not None:
        provenance["repo_relative_path"] = repo_relative_package
        return provenance

    recipe_paths = {
        "score": score,
        "identity_manifest": identity_manifest,
        "arrangement": arrangement,
        "capability_profile": capability_profile,
    }
    if all(path is not None for path in recipe_paths.values()):
        repo_relative_inputs = {
            name: _repo_relative(path) for name, path in recipe_paths.items() if path is not None
        }
        if all(value is not None for value in repo_relative_inputs.values()):
            data = package_data if package_data is not None else {}
            # score / arrangement: performance_package.json に raw bytes 相当の
            # pin がないため pin_sha256=None（existence-only）。この2種の内容
            # 自体は下の合成 pin 突合（derived_score / preservation_contract）
            # で別途検証する。
            pins: dict[str, Optional[str]] = {
                "score": None,
                "identity_manifest": _package_input_pin(data, "identity_manifest"),
                "arrangement": None,
                "capability_profile": _package_input_pin(data, "capability_profile"),
            }
            failures: list[str] = []
            # 検証で読んだ raw bytes を保持し、(b) の再計算と build_recipe の
            # per-input sha256 記録の両方に再利用する（単一 read 規律 — Codex
            # 6R P2 review #191: 検証と hash 記録で二重読みしない）。
            input_bytes: dict[str, bytes] = {}
            for name, path in recipe_paths.items():
                assert path is not None  # narrowed by the `all(...)` check above
                raw_bytes, reason = _verify_recipe_input(path, pin_sha256=pins[name])
                if raw_bytes is not None:
                    input_bytes[name] = raw_bytes
                if reason is not None:
                    failures.append(f"{name}: {reason}")

            if not failures:
                # 幾何非依存の合成 pin 再計算（Codex 4R P2 review #191,
                # discussion_r3610170990）: score/arrangement -> derived_score、
                # identity_manifest/arrangement -> preservation_contract を
                # package と同じ経路で再計算し pin と突合する。package_data が
                # 読めない/pin 自体が欠落している場合は正当性を主張できないため
                # 検証失敗として扱う（build_recipe を emit しない）。
                score_bytes = input_bytes["score"]
                identity_manifest_bytes = input_bytes["identity_manifest"]
                arrangement_bytes = input_bytes["arrangement"]

                expected_derived_score = _package_input_pin(data, "derived_score")
                if expected_derived_score is None:
                    failures.append(
                        "derived_score: performance_package.json has no readable "
                        "inputs.derived_score.sha256 pin to verify --score/"
                        "--arrangement against"
                    )
                else:
                    recomputed_derived_score = _recompute_derived_score_sha256(
                        score_bytes, arrangement_bytes
                    )
                    if recomputed_derived_score != expected_derived_score:
                        failures.append(
                            "derived_score: recomputed sha256 from --score/"
                            f"--arrangement ({recomputed_derived_score!r}) does not "
                            "match package-pinned inputs.derived_score.sha256 "
                            f"{expected_derived_score}"
                        )

                expected_contract = _package_input_pin(data, "preservation_contract")
                if expected_contract is None:
                    failures.append(
                        "preservation_contract: performance_package.json has no "
                        "readable inputs.preservation_contract.sha256 pin to verify "
                        "--identity-manifest/--arrangement against"
                    )
                else:
                    recomputed_contract = _recompute_preservation_contract_sha256(
                        identity_manifest_bytes, arrangement_bytes
                    )
                    if recomputed_contract != expected_contract:
                        failures.append(
                            "preservation_contract: recomputed sha256 from "
                            f"--identity-manifest/--arrangement ({recomputed_contract!r}) "
                            "does not match package-pinned "
                            f"inputs.preservation_contract.sha256 {expected_contract}"
                        )

                # svprpe package が auto-load する device profile
                # (Codex 5R P2 review #191, discussion_r3610193512): score/
                # arrangement には一切現れない隠れ入力なので、旧 4 入力の検証を
                # 全て通っても device profile だけが差し替わっていれば
                # recompile 結果の package sha256 がずれる。
                device_profile_recipe: Optional[dict[str, dict[str, str]]] = None
                device_profile_auto_load_note: Optional[str] = None
                expected_device_profile = data.get("inputs", {}).get("device_profile")
                if not isinstance(expected_device_profile, dict):
                    failures.append(
                        "device_profile: performance_package.json has no readable "
                        "inputs.device_profile entry to verify the auto-loaded "
                        "device profile (config/device_profiles/<generator>.yaml) "
                        "against"
                    )
                else:
                    recomputed_device_profile = _recompute_device_profile_pin(
                        score_bytes, arrangement_bytes
                    )
                    if recomputed_device_profile is None:
                        failures.append(
                            "device_profile: could not recompute the auto-loaded "
                            "device profile pin from --score/--arrangement"
                        )
                    else:
                        (
                            recomputed_generator,
                            recomputed_status,
                            recomputed_sha256,
                        ) = recomputed_device_profile
                        expected_generator = expected_device_profile.get("generator")
                        expected_status = expected_device_profile.get("status")
                        expected_sha256 = expected_device_profile.get("sha256")
                        if (
                            recomputed_generator != expected_generator
                            or recomputed_status != expected_status
                            or recomputed_sha256 != expected_sha256
                        ):
                            failures.append(
                                "device_profile: recomputed auto-loaded device "
                                f"profile (generator={recomputed_generator!r}, "
                                f"status={recomputed_status!r}, "
                                f"sha256={recomputed_sha256!r}) does not match "
                                "package-pinned inputs.device_profile "
                                f"(generator={expected_generator!r}, "
                                f"status={expected_status!r}, "
                                f"sha256={expected_sha256!r})"
                            )
                        elif recomputed_status == "loaded":
                            device_profile_relative_path = (
                                f"config/device_profiles/{recomputed_generator}.yaml"
                            )
                            device_profile_absolute_path = ROOT / device_profile_relative_path
                            if not device_profile_absolute_path.is_file():
                                # `load_device_profile` succeeded, so this should be
                                # unreachable in practice; fail closed rather than
                                # emit a recipe entry without a verified sha256.
                                failures.append(
                                    "device_profile: "
                                    f"{device_profile_relative_path} does not exist "
                                    "or is not a regular file (unexpected: "
                                    "load_device_profile reported it as loaded)"
                                )
                            else:
                                # device profile の raw bytes は他 4 入力と異なり
                                # 検証フローでまだ読んでいない新規入力（(c) の pin
                                # 突合は canonical JSON hash であり raw bytes では
                                # ない）ため、ここで 1 回だけ読む（Codex 6R P2
                                # review #191: 単一 read 規律 — 追加読みは 1 回に
                                # 統合する）。
                                device_profile_raw_bytes = (
                                    device_profile_absolute_path.read_bytes()
                                )
                                device_profile_recipe = {
                                    "device_profile": {
                                        "path": device_profile_relative_path,
                                        "sha256": hashlib.sha256(
                                            device_profile_raw_bytes
                                        ).hexdigest(),
                                    }
                                }
                                device_profile_auto_load_note = (
                                    "device_profile is auto-loaded by `svprpe "
                                    "package` from config/device_profiles/"
                                    "<generator>.yaml based on the resolved "
                                    "rendering.target_backend; it is not a "
                                    "compile_command argument, but is required for "
                                    "byte-identical reproduction and is pinned "
                                    "separately as inputs.device_profile in "
                                    "performance_package.json (Codex 5R P2 review "
                                    "#191, discussion_r3610193512)."
                                )
                        else:
                            device_profile_auto_load_note = (
                                "no config/device_profiles/"
                                f"{recomputed_generator}.yaml exists for generator "
                                f"{recomputed_generator!r}; svprpe package's "
                                "device-profile auto-load is a legitimate no-op for "
                                "this recipe (inputs.device_profile.status="
                                "'not_found')."
                            )

            if not failures:
                # 検証済み per-input sha256 を焼き込む（Codex 6R P2 review #191:
                # repo 相対パスのみでは、後年 checkout で入力の中身が変わっても
                # 最終 package sha256 の不一致でしか検知できず、どの入力が
                # ドリフトしたか特定できない欠陥への修正）。sha256 は上の検証
                # ループで既に読んだ ``input_bytes`` から取る（単一 read 規律 —
                # ここで再度ファイルを読まない）。compile_command 用の
                # ``repo_relative_inputs``（plain path 文字列）はそのまま流用。
                recipe_inputs: dict[str, dict[str, str]] = {
                    name: {
                        "path": repo_relative_inputs[name],
                        "sha256": hashlib.sha256(input_bytes[name]).hexdigest(),
                    }
                    for name in recipe_paths
                }
                if device_profile_recipe is not None:
                    recipe_inputs.update(device_profile_recipe)
                provenance["build_recipe"] = {
                    "inputs": recipe_inputs,
                    "compile_command": (
                        "svprpe package {score} {identity_manifest} {arrangement} "
                        "--capability-profile {capability_profile} --output-dir <output-dir>"
                    ).format(**repo_relative_inputs),
                }
                if device_profile_auto_load_note is not None:
                    provenance["build_recipe"]["auto_loaded_inputs"] = {
                        "device_profile": device_profile_auto_load_note
                    }
                # `--output-dir <output-dir>` の compile_command プレースホルダは
                # 自由な値ではない: channel_artifacts を持つ package では
                # `artifact_base.locator` が
                # `os.path.relpath(identity_manifest の親 dir, --output-dir)`
                # として package sha256 に焼き込まれているため、別 geometry から
                # recompile すると pinned sha256 を再現しない (Codex 7R P2 review
                # #191, discussion_r3610250841)。locator が存在すればその制約を
                # 明記し、channel_artifacts が空（locator ゼロ）の場合も
                # 「未検討」ではなく「幾何非依存であることを確認済み」と明示する。
                artifact_base_locators = _artifact_base_locators(data)
                if artifact_base_locators:
                    provenance["build_recipe"]["output_geometry"] = {
                        "artifact_base_locator": (
                            artifact_base_locators[0]
                            if len(artifact_base_locators) == 1
                            else artifact_base_locators
                        ),
                        "constraint": (
                            "choose --output-dir so that "
                            "os.path.relpath(<identity_manifest's parent dir>, "
                            "<output-dir>) equals artifact_base_locator; "
                            "performance_package.sha256 depends on this relative "
                            "geometry (Codex 7R P2 review #191, "
                            "discussion_r3610250841)."
                        ),
                    }
                else:
                    provenance["build_recipe"]["output_geometry"] = {
                        "note": (
                            "no channel artifact locators (channel_artifacts is "
                            "empty for this package, e.g. because no anchor's "
                            "delivery status is delivered/experimental); "
                            "performance_package.sha256 is independent of "
                            "--output-dir geometry for this recipe (Codex 7R P2 "
                            "review #191, discussion_r3610250841)."
                        )
                    }
                return provenance
            provenance["note"] = (
                "recipe inputs failed verification: "
                + "; ".join(failures)
                + ". Falling back to sha256-only provenance rather than emitting a "
                "possibly-false reproduction recipe (Codex 3R/4R/5R P2 review #191, "
                "discussion_r3610153978 / discussion_r3610170990 / "
                "discussion_r3610193512)."
            )
            return provenance

    provenance["note"] = (
        "performance_package.json itself is not committed (ephemeral build "
        "artifact, byte-for-byte reproducible via `svprpe package` from the "
        "committed score/identity_manifest/arrangement/capability-profile "
        "inputs recorded in ar4_plan.yaml#prompt_source.compiled_via); only "
        "its sha256 is pinned here as provenance, matching the WAV "
        "audio_sha256-pin convention (DD-A)."
    )
    return provenance


def build_takes_manifest(
    result: dict[str, Any],
    *,
    fixture_id: str,
    package_path: Path,
    package_sha256: str,
    package_data: Optional[dict[str, Any]] = None,
    score: Optional[Path] = None,
    identity_manifest: Optional[Path] = None,
    arrangement: Optional[Path] = None,
    capability_profile: Optional[Path] = None,
) -> dict[str, Any]:
    """`generate_ar4_takes` の戻り値から、コミット対象の
    ``ar4_takes_manifest.json`` 用ペイロード（timestamps を除く）を組み立てる。

    ``package_data``（`load_package_prompt` が読んだ `performance_package.json`
    の生 JSON dict）は `build_package_provenance` の pin 突合に使う。省略時は
    pin 突合なしの existence-only 検証になる（テストの後方互換用 — 実際の
    `_cmd_generate` からは常に渡される）。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "fixture_id": fixture_id,
        "performance_package": build_package_provenance(
            package_path,
            package_sha256,
            package_data,
            score=score,
            identity_manifest=identity_manifest,
            arrangement=arrangement,
            capability_profile=capability_profile,
        ),
        "prompt": result["prompt"],
        "model_id": result["model_id"],
        "model_revision": result["model_revision"],
        "duration_seconds": result["duration_seconds"],
        "guidance_scale": result["guidance_scale"],
        "samples": result["samples"],
    }


def build_generation_timestamps(result: dict[str, Any]) -> dict[str, Any]:
    """`generate_ar4_takes` の戻り値から、コミット対象の
    ``ar4_generation_timestamps.yaml`` 用ペイロードを組み立てる。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generation_calls": result["timestamps"],
    }


def _generate_output_paths(
    output_dir: Path,
    manifest_out: Path,
    timestamps_out: Optional[Path],
    take_indices: list[int],
) -> list[Path]:
    """`generate` サブコマンドが書き込みうる全出力パスを、生成を一切始める前に
    列挙する（``resolve()`` するだけで存在確認はしない — まだ存在しないファ
    イルも列挙対象に含める）。

    各 take の WAV 出力先は `generate_ar4_takes` の命名規則
    (``output_dir / f"take{take_index}.wav"``) をそのまま踏襲する。生成本体
    を実際に呼ぶと torch が必要になり衝突検証が torch なしでテストできなく
    なるため、ここでパス計算だけをロジックとして複製している（Codex 8R P2
    review #191: `--timestamps-out` が `--manifest-out` と同一パスの場合、
    manifest 書き込み後に timestamps 書き込みが silent に上書きし、両方書け
    たと報告しながら takes manifest が失われる欠陥への修正。生成本体を呼ばず
    に検証関数を単体テストできるよう分離する）。
    """
    paths = [output_dir.resolve(), manifest_out.resolve()]
    if timestamps_out is not None:
        paths.append(timestamps_out.resolve())
    for take_index in take_indices:
        paths.append((output_dir / f"take{take_index}.wav").resolve())
    return paths


def _reject_generate_output_collision(
    output_paths: list[Path], input_paths: list[Path]
) -> None:
    """出力パス同士、および出力パスと provenance 入力パスの衝突を、何も生成/
    書き込みしていない時点で検出する。

    `observe` の `_reject_observe_output_collision`
    (`src/svp_rpe/cli/observe_cmd.py`) と同型のガード: resolved-path 等値に
    加え、両者が実在するファイルなら `os.path.samefile`
    (同一 ``st_dev``/``st_ino``) でハードリンクも検出する。samefile が判定
    不能（片方が消えた等の `OSError`）な場合は例外を投げず弱いチェック
    (resolved-path 等値) にフォールバックする — 判定不能が CLI をクラッシュ
    させてはならない。

    最初に見つかった衝突 1 件で ``ValueError`` を送出して打ち切る（呼び出し
    側はこれを 1 行の stderr メッセージにして exit 1 する。Codex 8R P2 review
    #191）。
    """
    import os

    def _same(path_a: Path, path_b: Path) -> bool:
        if path_a == path_b:
            return True
        if path_a.exists() and path_b.exists():
            try:
                return os.path.samefile(path_a, path_b)
            except OSError:
                return False
        return False

    for index, path_a in enumerate(output_paths):
        for path_b in output_paths[index + 1 :]:
            if _same(path_a, path_b):
                raise ValueError(f"output paths collide: {path_a} == {path_b}")

    for output_path in output_paths:
        for input_path in input_paths:
            if _same(output_path, input_path):
                raise ValueError(
                    f"output path collides with input path: {output_path} == {input_path}"
                )


def _cmd_generate(args: argparse.Namespace) -> int:
    try:
        prompt_text, package_sha256, package_data = load_package_prompt(args.package)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    take_indices = args.take_index if args.take_index else [0, 1]

    # Codex 8R P2 review #191: 生成 (torch 推論) を一切始める前に、全出力パス
    # 同士および出力パスと provenance 入力パス (package/score/identity-manifest/
    # arrangement/capability-profile) の衝突を検証する。`--timestamps-out` が
    # `--manifest-out` と同一パスだと manifest 書き込み後に timestamps 書き込
    # みが silent に上書きし、両方書けたと報告しながら takes manifest が失わ
    # れる — ここで fail-closed に拒否し、何も書かない。
    output_paths = _generate_output_paths(
        args.output_dir, args.manifest_out, args.timestamps_out, take_indices
    )
    input_paths = [
        args.package.resolve(),
        args.score.resolve(),
        args.identity_manifest.resolve(),
        args.arrangement.resolve(),
        args.capability_profile.resolve(),
    ]
    try:
        _reject_generate_output_collision(output_paths, input_paths)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        result = generate_ar4_takes(
            prompt_text,
            output_dir=args.output_dir,
            take_indices=take_indices,
            seed_base=args.seed_base,
            model_id=args.model_id,
            model_revision=args.model_revision,
            duration_seconds=args.duration_seconds,
            guidance_scale=args.guidance_scale,
        )
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    manifest = build_takes_manifest(
        result,
        fixture_id=args.fixture_id,
        package_path=args.package,
        package_sha256=package_sha256,
        package_data=package_data,
        score=args.score,
        identity_manifest=args.identity_manifest,
        arrangement=args.arrangement,
        capability_profile=args.capability_profile,
    )
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(manifest['samples'])} take(s) to {args.output_dir}")
    print(f"wrote manifest to {args.manifest_out}")

    if args.timestamps_out is not None:
        timestamps = build_generation_timestamps(result)
        args.timestamps_out.parent.mkdir(parents=True, exist_ok=True)
        args.timestamps_out.write_text(
            json.dumps(timestamps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote generation timestamps to {args.timestamps_out}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "AR4 実観測バッチ orchestration (MusicGen). CI では実行しない — "
            "package が compile した prompt を読み、n take を生成し、"
            "sha256/タイムスタンプを記録する。"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate",
        help="performance_package.json の prompt から MusicGen で take を生成する（torch 必須）",
    )
    generate_parser.add_argument(
        "--package", type=Path, required=True, help="performance_package.json path"
    )
    generate_parser.add_argument(
        "--output-dir", type=Path, required=True, help="生成 WAV の書き出し先"
    )
    generate_parser.add_argument(
        "--manifest-out", type=Path, required=True, help="takes manifest JSON の出力先"
    )
    generate_parser.add_argument(
        "--timestamps-out",
        type=Path,
        default=None,
        help="生成タイムスタンプ JSON の出力先（省略時は書き出さない）",
    )
    generate_parser.add_argument(
        "--take-index",
        type=int,
        action="append",
        default=None,
        metavar="N",
        help="生成する take index（複数指定可・0-indexed）。省略時は [0, 1]（AR4 canonical n=2）",
    )
    generate_parser.add_argument(
        "--seed-base", type=int, default=DEFAULT_SEED_BASE, help="seed = seed_base + take_index（既定 8000）"
    )
    generate_parser.add_argument(
        "--model-id", type=str, default=DEFAULT_MODEL_ID, help="HuggingFace model id"
    )
    generate_parser.add_argument(
        "--model-revision", type=str, default=None, help="pin する revision（推奨: 40 桁 commit hash）"
    )
    generate_parser.add_argument(
        "--duration-seconds", type=float, default=12.0, help="生成長 [秒]（既定 12.0）"
    )
    generate_parser.add_argument(
        "--guidance-scale", type=float, default=3.0, help="classifier-free guidance scale（既定 3.0）"
    )
    generate_parser.add_argument(
        "--fixture-id", type=str, default=DEFAULT_FIXTURE_ID, help="manifest の fixture_id"
    )
    generate_parser.add_argument(
        "--score",
        type=Path,
        default=DEFAULT_SCORE,
        help=(
            "provenance recording 専用（生成には使わない — prompt は --package から"
            "読む）: performance_package.json をコンパイルした score YAML のパス。"
            "manifest の performance_package.build_recipe に repo 相対で記録する"
        ),
    )
    generate_parser.add_argument(
        "--identity-manifest",
        type=Path,
        default=DEFAULT_IDENTITY_MANIFEST,
        help="provenance recording 専用: performance_package.json をコンパイルした identity manifest YAML のパス",
    )
    generate_parser.add_argument(
        "--arrangement",
        type=Path,
        default=DEFAULT_ARRANGEMENT,
        help="provenance recording 専用: performance_package.json をコンパイルした arrangement spec YAML のパス",
    )
    generate_parser.add_argument(
        "--capability-profile",
        type=Path,
        default=DEFAULT_CAPABILITY_PROFILE,
        help="provenance recording 専用: performance_package.json をコンパイルした capability profile YAML のパス",
    )
    generate_parser.set_defaults(func=_cmd_generate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
