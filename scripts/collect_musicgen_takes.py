"""scripts/collect_musicgen_takes.py — MusicGen ローカル生成 runbook.

**手動 runbook。CI では絶対に実行しない。** fixture（数値）→ grip の区間だけが
決定論・CI 対象（DD-A、`docs/controllability_poc.md` §4 踏襲）。`generate` サブ
コマンドは torch 経由の非決定論的推論を行い、`pip install -e ".[musicgen]"`
（`facebook/musicgen-small` 等の重みは HuggingFace から実行時取得——パッケージ
には同梱しない）が必要。`extract` サブコマンドは torch 不要（librosa 経由の
既存 RPE 抽出のみ）で K2 と同一スキーマの fixture を書き出す。

参考パターン: `scripts/collect_clap_fixture.py`（手動 runbook・sha256 は書き出し
たバイトから必ず計算・manifest pin 照合 fail-fast）、`scripts/build_k1_fixture.py`
（決定論 seed = f(knob_index, level_index, repeat)）、
`examples/control/k2/suno_rpe_fixture.json`（fixture スキーマの雛形）。

plan.yaml スキーマ:

    schema_version: "1.0"
    fixture_id: k2_musicgen_mini
    generator: musicgen-small
    model_id: facebook/musicgen-small
    model_revision: null            # 省略可・pin 推奨
    duration_seconds: 12.0
    guidance_scale: 3.0
    repetitions: 8
    knobs:
      - name: bpm
        sensor: bpm
        low_level: "90"
        high_level: "170"
        expected_sign: 1
        prompt_low: "..."
        prompt_high: "..."
        # kind は optional（既定 "effect_size"）。"categorical" にすると fixture の
        # knob spec へ透過され、measure_grip.py が一致率（match_rate）経路で解析する
        # （K1 key 前例。一致率経路では expected_sign は未使用 — 中立の 0 を推奨）。
        kind: categorical

Usage:
    # 1) 生成（手動・torch 必須。実推論なので毎回時間がかかる）
    python scripts/collect_musicgen_takes.py generate \\
        --plan examples/control/k2_musicgen/plan.yaml \\
        --output-dir /tmp/musicgen_takes \\
        --manifest-out /tmp/musicgen_takes/takes_manifest.json

    # 2) 抽出（手動・torch 不要）
    python scripts/collect_musicgen_takes.py extract \\
        --manifest /tmp/musicgen_takes/takes_manifest.json \\
        --audio-dir /tmp/musicgen_takes \\
        --fixture-out /tmp/musicgen_takes/fixture.json

    # 3) R3 repetition harness 向け: 1 スコアから N テイク生成（手動・torch 必須）
    python scripts/collect_musicgen_takes.py perform \\
        --score examples/roundtrip/synth_01_source.yaml \\
        --repetitions 5 \\
        --output-dir /tmp/musicgen_takes/rep \\
        --manifest-out /tmp/musicgen_takes/rep/takes_manifest.json

    # takes manifest を svprpe roundtrip-rep に渡すと R3-1/2/3 の計器で測定できる
    # （torch 不要）:
    #   svprpe roundtrip-rep examples/roundtrip/synth_01_source.yaml \\
    #       /tmp/musicgen_takes/rep/takes_manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Optional

from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SCHEMA_VERSION = "1.0"
GENERATOR = "collect_musicgen_takes.py"
_INSTALL_HINT = "pip install -e '.[musicgen]'"

# コミット済みの device profile / grip fixture が実測したモデルと revision
# （PR B, 2026-07-03、docs/musicgen_backend.md §5 の G4 目視確認と同一 pin）。
# `device_profiles/musicgen.yaml` は backend seam（target_backend: musicgen）で
# キーされるため、コンパイル側は演奏モデルの粒度を知らない。モデル id /
# revision を知る唯一の場所である runbook が、実測スコープ外への適用を advisory する。
PROFILE_MEASURED_MODEL_ID = "facebook/musicgen-small"
PROFILE_MEASURED_MODEL_REVISION = "4c8334b02c6ec4e8664a91979669a501ec497792"


def profile_scope_advisory(model_id: str, model_revision: Optional[str] = None) -> Optional[str]:
    """実測スコープ外の MusicGen モデル / revision に対する注意喚起文を返す。

    `device_profiles/musicgen.yaml` の control_defaults / knob_quirks と
    `examples/control/k2_musicgen/*` の grip は ``PROFILE_MEASURED_MODEL_ID``
    の ``PROFILE_MEASURED_MODEL_REVISION``（small・R=8）のみの実測に基づく。
    medium / large 等の未計測バリアント、または未 pin（``None``）/ 別 revision で
    生成する場合、コンパイル時の tight/loose 既定や高テンポ halving advisory が
    転移する保証はない — stderr にのみ知らせ、生成やプロンプト本文は変えない
    （#128 の本文不変規律）。model id と revision が両方一致するときのみ ``None``。
    """
    scope = (
        f"device_profiles/musicgen.yaml and examples/control/k2_musicgen/* are measured on "
        f"{PROFILE_MEASURED_MODEL_ID} @ {PROFILE_MEASURED_MODEL_REVISION[:8]} only"
    )
    if model_id != PROFILE_MEASURED_MODEL_ID:
        return (
            f"note: {scope}; {model_id} is unmeasured, so compile-time grip defaults / "
            "advisories may not transfer (docs/musicgen_backend.md §7.1)."
        )
    if model_revision is None:
        return (
            f"note: {scope}; running without --model-revision resolves to the current HF "
            "head, which may have advanced past the measured revision — pin "
            f"--model-revision {PROFILE_MEASURED_MODEL_REVISION} to stay in measured scope "
            "(docs/musicgen_backend.md §7.1)."
        )
    if model_revision != PROFILE_MEASURED_MODEL_REVISION:
        return (
            f"note: {scope}; revision {model_revision} is unmeasured, so compile-time grip "
            "defaults / advisories may not transfer (docs/musicgen_backend.md §7.1)."
        )
    return None


class KnobPlan(BaseModel):
    """plan.yaml の 1 ノブ定義。未知キーは fail-fast で拒否する。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    sensor: str
    low_level: str
    high_level: str
    expected_sign: int
    prompt_low: str
    prompt_high: str
    kind: str = "effect_size"


class GenerationPlan(BaseModel):
    """plan.yaml のトップレベルスキーマ。未知キーは fail-fast で拒否する。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    fixture_id: str
    generator: str
    model_id: str
    model_revision: Optional[str] = None
    duration_seconds: float
    guidance_scale: float
    repetitions: int
    knobs: list[KnobPlan]


def load_plan(path: Path) -> GenerationPlan:
    """plan.yaml/json を読み込み、`GenerationPlan` として検証する（fail-fast）。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"plan must be a JSON/YAML mapping: {path}")
    return GenerationPlan.model_validate(raw)


def sample_seed(knob_index: int, level_index: int, repeat: int) -> int:
    """(ツマミ, 水準, 反復) ごとに安定な seed を割り当てる。

    `scripts/build_k1_fixture.py` の ``sample_seed`` と同型の決定論導出
    （``knob_index * 100 + level_index * 50 + repeat`` で構成）。``repeat`` は
    0-indexed（``range(repetitions)``）。``repetitions < 50`` の間は
    ``(knob_index, level_index, repeat)`` の全組み合わせで一意になる
    （``level_index`` は 0/1 の 2 値のみを取るため、隣接する
    ``level_index`` 帯が重ならない上限が 50）。
    """
    return 1000 + knob_index * 100 + level_index * 50 + repeat


class PlanTake(NamedTuple):
    """`iter_plan_takes` が列挙する 1 テイク分の生成指示（純データ・torch 不要）。"""

    knob_index: int
    knob: KnobPlan
    level_index: int
    level_token: str
    level_value: str
    prompt: str
    repeat: int
    seed: int


def iter_plan_takes(
    plan: GenerationPlan,
    *,
    only_level: Optional[str] = None,
    only_repeat: Optional[int] = None,
) -> Iterator[PlanTake]:
    """plan の全 (knob, level, repeat) を決定論順に列挙する（フィルタ付き・純ロジック）。

    長時間バッチをチャンク分割実行するためのフィルタ:
    ``only_level``（"low"/"high"）と ``only_repeat``（0-indexed）で部分集合だけを
    生成できる。**seed はフィルタに依存しない** — `sample_seed` は絶対インデックス
    （knob_index, level_index, repeat）から導出するため、チャンク分割しても
    一括実行と同一の seed / sample_id 系列になる（K2-seg 運用ノートの復旧耐性と
    同じ性質。分割の仕方は結果に影響しない）。
    """
    if only_level is not None and only_level not in ("low", "high"):
        raise ValueError(f"only_level must be 'low' or 'high', got {only_level!r}")
    if only_repeat is not None and not 0 <= only_repeat < plan.repetitions:
        raise ValueError(
            f"only_repeat must be within [0, {plan.repetitions}), got {only_repeat}"
        )

    for knob_index, knob in enumerate(plan.knobs):
        levels = (
            ("low", knob.low_level, knob.prompt_low),
            ("high", knob.high_level, knob.prompt_high),
        )
        for level_index, (level_token, level_value, prompt) in enumerate(levels):
            if only_level is not None and level_token != only_level:
                continue
            for repeat in range(plan.repetitions):
                if only_repeat is not None and repeat != only_repeat:
                    continue
                yield PlanTake(
                    knob_index=knob_index,
                    knob=knob,
                    level_index=level_index,
                    level_token=level_token,
                    level_value=level_value,
                    prompt=prompt,
                    repeat=repeat,
                    seed=sample_seed(knob_index, level_index, repeat),
                )


def merge_takes_manifests(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """チャンク実行が書き出した takes manifest を単一 manifest へ統合する。

    ヘッダ（schema_version / fixture_id / generator / plan）が完全一致しない
    manifest 同士の統合は fail-fast（別 plan のサンプル混入防止）。sample_id の
    重複も fail-fast（同一チャンクの二重実行の検出）。統合後の samples は
    plan 由来の正準順（knob 順 → low/high → repeat 昇順）にソートするため、
    **チャンクの実行順によらず一括実行と同一の manifest になる**。
    """
    for key in ("schema_version", "fixture_id", "generator", "plan"):
        if existing.get(key) != new.get(key):
            raise ValueError(
                f"cannot merge manifests: {key} differs "
                f"(existing={existing.get(key)!r}, new={new.get(key)!r})"
            )

    existing_ids = {sample["sample_id"] for sample in existing["samples"]}
    duplicates = [
        sample["sample_id"] for sample in new["samples"] if sample["sample_id"] in existing_ids
    ]
    if duplicates:
        raise ValueError(
            f"cannot merge manifests: duplicate sample_id(s) {duplicates} "
            "(same chunk generated twice?)"
        )

    plan_raw = existing["plan"]
    knob_order = {knob["name"]: index for index, knob in enumerate(plan_raw["knobs"])}

    def _canonical_order(sample: dict[str, Any]) -> tuple[int, int, int]:
        knob_index = knob_order[sample["knob"]]
        knob = plan_raw["knobs"][knob_index]
        level_index = 0 if sample["level"] == knob["low_level"] else 1
        return (knob_index, level_index, int(sample["repeat"]))

    merged = dict(existing)
    merged["samples"] = sorted(
        list(existing["samples"]) + list(new["samples"]), key=_canonical_order
    )
    return merged


def generator_label(model_id: str) -> str:
    """model_id から generator ラベルを導出する（例: facebook/musicgen-medium → musicgen-medium）。

    `perform` の takes manifest はこのラベルを `generator` に記録し、
    `svprpe roundtrip-rep` がそのままレポートへ転記する。model_id と乖離した
    固定ラベルは機種間比較（device profile / grip の帰属）を汚すため、
    必ず要求されたモデルから導出する。
    """
    return model_id.rsplit("/", 1)[-1]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _import_musicgen_stack() -> Any:
    """torch/transformers は `generate` 実行時にのみ import する。

    モジュールトップで import すると、この runbook を単に import しただけの
    テスト（torch なし環境）まで巻き添えで壊れる。ImportError はここで
    catch せず、呼び出し側（``generate_takes`` / ``_cmd_generate``）が
    案内メッセージ付きで処理する。
    """
    try:
        import torch
        from transformers import AutoProcessor, MusicgenForConditionalGeneration
    except ImportError as exc:
        raise ImportError(
            f"MusicGen generation requires the `musicgen` extra ({_INSTALL_HINT}); {exc}"
        ) from exc
    return torch, AutoProcessor, MusicgenForConditionalGeneration


def generate_takes(
    plan: GenerationPlan,
    *,
    output_dir: Path,
    only_level: Optional[str] = None,
    only_repeat: Optional[int] = None,
) -> dict[str, Any]:
    """plan の (knob, level, repeat) について MusicGen 推論を行い、takes manifest を返す。

    音声は ``output_dir`` に WAV として書き出すのみでコミット対象にはしない。
    manifest の ``audio_sha256`` は書き出したバイト列から必ず計算する
    （``collect_clap_fixture.py`` と同じ規約）。

    ``only_level`` / ``only_repeat`` でチャンク分割実行できる（実行環境のコマンド
    タイムアウトより 1 チャンクを短くするため）。seed は絶対インデックス由来
    （`iter_plan_takes` 参照）のため分割は結果に影響せず、チャンク manifest は
    `merge_takes_manifests` で一括実行と同一の manifest に統合できる。
    """
    takes = list(iter_plan_takes(plan, only_level=only_level, only_repeat=only_repeat))

    torch, AutoProcessor, MusicgenForConditionalGeneration = _import_musicgen_stack()
    from scipy.io import wavfile

    processor = AutoProcessor.from_pretrained(plan.model_id, revision=plan.model_revision)
    model = MusicgenForConditionalGeneration.from_pretrained(
        plan.model_id, revision=plan.model_revision
    )
    model.eval()
    sampling_rate = int(model.config.audio_encoder.sampling_rate)
    # ベストエフォートで解決済みの実 revision を記録する（transformers が
    # from_pretrained で pin した commit hash）。取得できなければ plan の
    # 宣言値へフォールバックする。
    resolved_revision = getattr(model.config, "_commit_hash", None) or plan.model_revision

    output_dir.mkdir(parents=True, exist_ok=True)
    max_new_tokens = int(plan.duration_seconds * 50)

    samples: list[dict[str, Any]] = []
    for take in takes:
        torch.manual_seed(take.seed)
        inputs = processor(text=[take.prompt], padding=True, return_tensors="pt")
        audio_values = model.generate(
            **inputs,
            do_sample=True,
            guidance_scale=plan.guidance_scale,
            max_new_tokens=max_new_tokens,
        )
        waveform = audio_values[0, 0].detach().cpu().numpy()
        sample_id = f"{take.knob.name}_{take.level_token}_{take.repeat:02d}"
        audio_path = output_dir / f"{sample_id}.wav"
        wavfile.write(str(audio_path), sampling_rate, waveform)
        audio_sha256 = _sha256_file(audio_path)
        samples.append(
            {
                "sample_id": sample_id,
                "knob": take.knob.name,
                "level": take.level_value,
                "repeat": take.repeat,
                "seed": take.seed,
                "prompt": take.prompt,
                "audio_path": audio_path.name,
                "audio_sha256": audio_sha256,
                "model_id": plan.model_id,
                "model_revision": resolved_revision,
                "duration_seconds": plan.duration_seconds,
                "guidance_scale": plan.guidance_scale,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_id": plan.fixture_id,
        "generator": plan.generator,
        "plan": plan.model_dump(),
        "samples": samples,
    }


def resolve_perform_target(
    score_path: Path, *, fixture_id: Optional[str] = None
) -> tuple[Any, str, str]:
    """スコアをロードし prompt / fixture slug を解決する（torch 不要）。

    `perform` サブコマンドのうち torch を必要としない部分（score のロードと
    `ExternalPromptAdapter` によるプロンプトレンダリング）を独立させ、torch
    なし環境でも単体テストできるようにする。backend override はしない —
    `score.rendering.target_backend` にそのまま従う（楽譜が語る）。
    """
    from svp_rpe.compose.loader import load_composition_score
    from svp_rpe.compose.prompt_renderer import ExternalPromptAdapter
    from svp_rpe.roundtrip.compare import normalize_label

    score = load_composition_score(score_path)
    prompt_text = ExternalPromptAdapter().render(score).text
    slug = _filename_safe_slug(
        fixture_id or normalize_label(score.meta.title).replace(" ", "-")
    )
    return score, prompt_text, slug


def _filename_safe_slug(raw: str) -> str:
    """slug を単一のファイル名コンポーネントとして安全な形に正規化する。

    `meta.title` 由来の既定 slug は `EDM/Rock` のようにパス区切りを含みうる。
    そのまま `sample_id` / `audio_path` に使うと `output_dir` 直下でない
    ネストしたパスへの書き込みになり、**高コストな生成の後に** WAV 書き出しで
    落ちる。英数字と `._-` 以外は `-` へ置換し、連続・端の `-`/`.` を刈り込む。
    """
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw)
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-.")
    return sanitized or "score"


def perform_takes(
    score_path: Path,
    *,
    repetitions: int,
    output_dir: Path,
    seed_base: int = 5000,
    duration_seconds: float = 12.0,
    guidance_scale: float = 3.0,
    fixture_id: Optional[str] = None,
    model_id: str = "facebook/musicgen-small",
    model_revision: Optional[str] = None,
) -> dict[str, Any]:
    """`score_path` のプロンプトから MusicGen で `repetitions` テイクを生成する。

    R3（`docs/roadmap_goal2.md`）確率的往復ハーネス向けの takes manifest を
    書き出す。`generate_takes` (K2 plan.yaml 経路) と異なり、ノブの
    low/high 水準ではなく単一スコアの単一プロンプトを繰り返し生成する
    （反復間で変わるのは `seed` のみ）。音声は `output_dir` に WAV として
    書き出すのみでコミット対象にはしない。manifest の `audio_sha256` は
    書き出したバイト列から必ず計算する（`collect_clap_fixture.py` と同じ規約）。

    `repetitions < 2` は**モデルロード・生成前に** fail-fast する — R3 反復
    レポート（`svprpe roundtrip-rep`）は n>1 でのみ成立するため、無効な
    手動バッチに生成時間を消費させてから解析側で落とすことを避ける。
    """
    if repetitions < 2:
        raise ValueError(
            f"R3 perform batch requires --repetitions >= 2; got {repetitions}. "
            "`svprpe roundtrip-rep` rejects n<2 batches, so generating them "
            "would only waste MusicGen inference time."
        )
    _, prompt_text, slug = resolve_perform_target(score_path, fixture_id=fixture_id)

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
    for repeat in range(repetitions):
        seed = seed_base + repeat
        torch.manual_seed(seed)
        inputs = processor(text=[prompt_text], padding=True, return_tensors="pt")
        audio_values = model.generate(
            **inputs,
            do_sample=True,
            guidance_scale=guidance_scale,
            max_new_tokens=max_new_tokens,
        )
        waveform = audio_values[0, 0].detach().cpu().numpy()
        sample_id = f"{slug}_{repeat:02d}"
        audio_path = output_dir / f"{sample_id}.wav"
        wavfile.write(str(audio_path), sampling_rate, waveform)
        audio_sha256 = _sha256_file(audio_path)
        samples.append(
            {
                "sample_id": sample_id,
                "repeat": repeat,
                "seed": seed,
                "audio_path": audio_path.name,
                "audio_sha256": audio_sha256,
                "duration_seconds": duration_seconds,
                "guidance_scale": guidance_scale,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_id": slug,
        "generator": generator_label(model_id),
        "score_path": str(score_path),
        "prompt": prompt_text,
        "model_id": model_id,
        "model_revision": resolved_revision,
        "samples": samples,
    }


def load_takes_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"takes manifest must be a JSON object: {path}")
    return raw


def extract_fixture(manifest: dict[str, Any], *, audio_dir: Path) -> dict[str, Any]:
    """takes manifest から K2 と同一スキーマの RPE 特徴量 fixture を組み立てる。

    torch 不要（``extract_physical_from_file`` の librosa 経路のみ）。各テイクの
    WAV バイトから sha256 を再計算し、manifest の ``audio_sha256`` と照合する
    （不一致は fail-fast — stale な pin を fixture が主張してしまう事故を防ぐ）。
    """
    from svp_rpe.rpe.extractor import extract_physical_from_file

    plan_raw = dict(manifest["plan"])
    knob_specs = []
    for knob in plan_raw["knobs"]:
        spec = {
            "name": knob["name"],
            "sensor": knob["sensor"],
            "low_level": knob["low_level"],
            "high_level": knob["high_level"],
            "expected_sign": knob["expected_sign"],
        }
        if "kind" in knob:
            spec["kind"] = knob["kind"]
        knob_specs.append(spec)

    samples: list[dict[str, Any]] = []
    for take in manifest["samples"]:
        audio_path = audio_dir / str(take["audio_path"])
        audio_bytes = audio_path.read_bytes()
        computed_sha256 = _sha256_bytes(audio_bytes)
        manifest_sha256 = str(take["audio_sha256"])
        if computed_sha256 != manifest_sha256:
            raise ValueError(
                f"sample {take['sample_id']!r}: manifest audio_sha256 does not match "
                f"the audio file at {audio_path} "
                f"(manifest={manifest_sha256}, computed={computed_sha256})"
            )
        physical = extract_physical_from_file(str(audio_path))
        observed_key = (
            f"{physical.key} {physical.mode}" if physical.key and physical.mode else "unknown"
        )
        samples.append(
            {
                "sample_id": take["sample_id"],
                "knob": take["knob"],
                "level": take["level"],
                "audio_sha256": computed_sha256,
                "features": {
                    "bpm": physical.bpm,
                    "key": observed_key,
                    "spectral_centroid": physical.spectral_centroid,
                    "spectral_profile": {"brightness": physical.spectral_profile.brightness},
                    "active_rate": physical.active_rate,
                    "valley_depth": physical.valley_depth,
                    "onset_density": physical.onset_density,
                    "time_signature": physical.time_signature,
                    "time_signature_confidence": physical.time_signature_confidence,
                },
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_id": plan_raw["fixture_id"],
        "generator": plan_raw["generator"],
        "repetitions": plan_raw["repetitions"],
        "knobs": knob_specs,
        "samples": samples,
    }


def _cmd_generate(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    advisory = profile_scope_advisory(plan.model_id, plan.model_revision)
    if advisory is not None:
        print(advisory, file=sys.stderr)
    try:
        manifest = generate_takes(
            plan,
            output_dir=args.output_dir,
            only_level=args.only_level,
            only_repeat=args.only_repeat,
        )
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.append and args.manifest_out.exists():
        existing = load_takes_manifest(args.manifest_out)
        manifest = merge_takes_manifests(existing, manifest)
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(manifest['samples'])} takes to {args.output_dir}")
    print(f"wrote manifest to {args.manifest_out}")
    return 0


def _cmd_perform(args: argparse.Namespace) -> int:
    advisory = profile_scope_advisory(args.model_id, args.model_revision)
    if advisory is not None:
        print(advisory, file=sys.stderr)
    try:
        manifest = perform_takes(
            args.score,
            repetitions=args.repetitions,
            output_dir=args.output_dir,
            seed_base=args.seed_base,
            duration_seconds=args.duration_seconds,
            guidance_scale=args.guidance_scale,
            fixture_id=args.fixture_id,
            model_id=args.model_id,
            model_revision=args.model_revision,
        )
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(manifest['samples'])} takes to {args.output_dir}")
    print(f"wrote manifest to {args.manifest_out}")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    manifest = load_takes_manifest(args.manifest)
    fixture = extract_fixture(manifest, audio_dir=args.audio_dir)
    args.fixture_out.parent.mkdir(parents=True, exist_ok=True)
    args.fixture_out.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.fixture_out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "MusicGen ローカル生成 runbook. CI では実行しない — 手動でテイクを生成し "
            "(generate) 数値 fixture を組み立てる (extract)。"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate", help="MusicGen でテイクを生成する（torch 必須・手動実行のみ）"
    )
    generate_parser.add_argument("--plan", type=Path, required=True, help="plan.yaml/json path")
    generate_parser.add_argument(
        "--output-dir", type=Path, required=True, help="生成 WAV の書き出し先"
    )
    generate_parser.add_argument(
        "--manifest-out", type=Path, required=True, help="takes manifest JSON の出力先"
    )
    generate_parser.add_argument(
        "--only-level",
        choices=("low", "high"),
        default=None,
        help="このレベルのテイクのみ生成する（チャンク分割実行用。seed は分割に不変）",
    )
    generate_parser.add_argument(
        "--only-repeat",
        type=int,
        default=None,
        metavar="N",
        help="この repeat インデックス（0-indexed）のテイクのみ生成する（チャンク分割実行用）",
    )
    generate_parser.add_argument(
        "--append",
        action="store_true",
        help=(
            "--manifest-out が既存ならヘッダ一致を検証してサンプルを統合する"
            "（正準順ソート・sample_id 重複は fail-fast）。チャンク分割実行の合流用"
        ),
    )
    generate_parser.set_defaults(func=_cmd_generate)

    extract_parser = subparsers.add_parser(
        "extract", help="テイクから RPE 特徴量 fixture を抽出する（torch 不要）"
    )
    extract_parser.add_argument(
        "--manifest", type=Path, required=True, help="generate が書き出した takes manifest"
    )
    extract_parser.add_argument("--audio-dir", type=Path, required=True, help="WAV の格納先")
    extract_parser.add_argument(
        "--fixture-out", type=Path, required=True, help="fixture JSON の出力先"
    )
    extract_parser.set_defaults(func=_cmd_extract)

    perform_parser = subparsers.add_parser(
        "perform",
        help=(
            "スコアのプロンプトから MusicGen で N テイクを生成する"
            "（torch 必須・手動実行のみ・R3 repetition harness 向け）"
        ),
    )
    perform_parser.add_argument(
        "--score", type=Path, required=True, help="Composition Score YAML path"
    )
    perform_parser.add_argument(
        "--repetitions", type=int, required=True, help="生成するテイク数 N"
    )
    perform_parser.add_argument(
        "--output-dir", type=Path, required=True, help="生成 WAV の書き出し先"
    )
    perform_parser.add_argument(
        "--manifest-out", type=Path, required=True, help="takes manifest JSON の出力先"
    )
    perform_parser.add_argument(
        "--seed-base", type=int, default=5000, help="seed = seed_base + repeat (既定 5000)"
    )
    perform_parser.add_argument(
        "--duration-seconds", type=float, default=12.0, help="生成長 [秒]（既定 12.0）"
    )
    perform_parser.add_argument(
        "--guidance-scale", type=float, default=3.0, help="classifier-free guidance scale（既定 3.0）"
    )
    perform_parser.add_argument(
        "--fixture-id",
        type=str,
        default=None,
        help="省略時は score.meta.title から slug を導出する",
    )
    perform_parser.add_argument(
        "--model-id", type=str, default="facebook/musicgen-small", help="HuggingFace model id"
    )
    perform_parser.add_argument(
        "--model-revision", type=str, default=None, help="pin する revision（省略可）"
    )
    perform_parser.set_defaults(func=_cmd_perform)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
