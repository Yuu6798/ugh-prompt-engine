# Learned Audio Annotation Layer Policy

Status: 実装済み — 5 adapter 採用（`beat_this` / `panns_inference` / `basic-pitch` /
`laion-clap` CLAP / `faster-whisper`）。CLAP は PR2b-1（隔離配線）→ PR2b-2（実推論・実
fixture 採取）まで closeout 済み。`faster-whisper` は配線 + 実推論スモーク済み
（スピーチ/器楽・フルミックス経路のみ。vocals 分離込み実 E2E と歌入り実音源は
実音源律速、`docs/lyrics_transcription_sensor.md` §8）
Scope: audio annotation models considered for `svp_rpe`
Audience: contributors adding learned-model backends

This document is the canonical adopt / reject / hold list for learned
audio models inside `svp_rpe`. It is referenced from
[`roadmap_goal1.md`](roadmap_goal1.md) (Q4').

## 1. Goal

Add a learned-model audio annotation layer that augments — but does not
replace — the existing rule-based RPE layers, using only OSS components
under MIT / Apache-style licenses.

`PhysicalRPE` and `SemanticRPE` remain the deterministic, evidence-bearing
path. Learned-model output is isolated in a separate
`LearnedAudioAnnotations` container attached to `RPEBundle`, and is
never folded back into rule-derived evidence fields.

Generator-side (performer) models — e.g. MusicGen — are out of scope for
this policy's annotation isolation principle: they produce audio, not
annotations, and their output only ever reaches `RPEBundle` through the
same rule-based extraction path as any other audio source. License
recording for generator-side models lives in
[`docs/musicgen_backend.md`](musicgen_backend.md).

## 2. Core Principle

Learned-model output MUST NOT be written into `SemanticRPE.por_surface`,
`semantic_hypothesis`, `PhysicalRPE.*`, or any other rule-derived
evidence field.

Rationale: rule-based propositions and external model estimates serve
different epistemic roles. Mixing them obscures which fields are
measured constraints and which are model-conditioned guesses, which
breaks the auditability of the evidence-bearing semantic layer
delivered in Q4 (PR #8).

A learned model's output is a tag with a confidence score and provenance.
It is never ground truth, and it is never a music-quality label.

To prevent surface-level confusion with the rule layer's
`SemanticLabel.evidence` field (which carries epistemic weight),
`LearnedAudioLabel` does NOT have a field called `evidence`. Free-form
provenance hints from a learned model live in `LearnedAudioLabel.notes`.

## 3. Adoption Matrix

### 3.1 Adopt

#### `beat_this`

- Use case: beat / downbeat detection
- License: MIT-compatible
- Constraints:
  - `dbn=False` MUST be fixed; we do not pull madmom DBN as a transitive
    dependency.
  - Ship behind an `optional` extra so the default install stays light.
  - Provide a deterministic fallback path (current librosa backend) so
    pipelines without the extra still produce beat output.
- Replaces: the `madmom` candidate in `roadmap_goal1.md` Q2-1 and Q5-2.

#### `panns_inference`

- Use case: AudioSet 527-class acoustic tags and embeddings
- Constraints:
  - Output is treated as `external acoustic tags`, not as mood / genre /
    music-quality ground truth.
  - Top-k labels and an optional embedding only; no full posterior dump.
  - Never written into `SemanticRPE` evidence layers.

#### `basic-pitch`

- Use case: polyphonic note events and melody contour reinforcement
- Constraints:
  - Additive only. The current `pyin` melody contour MUST NOT be
    replaced in the same change set.
  - Ship behind an `optional` extra.

#### `laion-clap` (CLAP)

- Use case: 意味層読解の補助センサー — prompt<->audio / score<->audio の
  cosine 適合度（学習版 grip）を計測する。`SemanticRPE` のルール版意味付けと
  相互検証するためのものであり、置き換えない。`LearnedAudioAnnotations.
  embedding` を初めて populate するアダプタ（`panns_inference` は embedding
  を受け取るが破棄している — `panns_adapter.py` 参照）。`svprpe extract
  --clap-semantic` から `rpe/learned/semantic_axes.py` 経由で SOURCE 音声を
  抽出段階で読み、固定の意味軸バッテリー（`config/semantic_probe_axes.yaml`）
  に対する `LearnedAudioAnnotations.semantic_axes` を populate する
  （詳細は [`docs/semantic_sensor_clap.md`](semantic_sensor_clap.md)）。
- License（verbatim findings, verified 2026-07-02）:
  - code: PyPI 配布物のメタデータに内部矛盾あり。
    `https://pypi.org/pypi/laion-clap/json` の `info.license` フィールドは
    CC0 1.0 Universal の全文をそのまま格納しており、GitHub 上の `LICENSE`
    ファイル（`https://raw.githubusercontent.com/LAION-AI/CLAP/main/LICENSE`）
    と一致する。一方で同 JSON の `classifiers` は
    `License :: OSI Approved :: Apache Software License` を宣言しており、
    `license` フィールドと矛盾する。どちらも許諾的ライセンスではあるが、
    この矛盾自体を発明せず記録する。
  - weights: Hugging Face
    （`https://huggingface.co/lukewys/laion_clap`）の repository-level
    license badge は `cc0-1.0`。ただし model card 本文は空で、
    `music_audioset_epoch_15_esc_90.14.pt` 等の個別 checkpoint について
    追加のライセンス文は見当たらない（verified 2026-07-02, PR2b-2）。
- Constraints:
  - torch と ~2GB の重みは optional extra `semantic-embed` に限定する。
    デフォルトインストールは変えない。laion-clap 1.1.7 は torch /
    torchvision を install_requires に宣言しない（上流メタデータ不備、
    PR2b-2 実採取で手動インストールが必要だったことから確認）ため、
    extra 側で明示的に同梱する — `pip install -e ".[semantic-embed]"`
    だけで採取環境が立つことが runbook の契約。
  - cosine 適合度は A/B コントラスト（`contrast_fit`）で読む — grip と同じ
    哲学で verdict を出さない。
  - fixture 駆動（`scripts/collect_clap_fixture.py`）で決定論区間を担保
    する。実推論は CI に持ち込まない。
  - music 系 checkpoint（`music_audioset_epoch_15_esc_90.14.pt` /
    `music_speech_*` 等）は上流 README
    （`https://github.com/LAION-AI/CLAP#pretrained-models`）が明記する通り
    `amodel="HTSAT-base"` の指定が必須（デフォルトの audio encoder
    とは形状が一致せず `load_ckpt` が shape mismatch で失敗する）。runbook
    例: `python scripts/collect_clap_fixture.py --checkpoint
    music_audioset_epoch_15_esc_90.14.pt --amodel HTSAT-base --manifest
    manifest.yaml --output fixture.json`。PR2b-2 の実行手順の一部。

#### `faster-whisper` (SYSTRAN)

- Use case: 歌詞の記号列内容を読む相補的な意味層センサー — CLAP が読む
  連続値 grip（`vocal_presence` 等）に対し、こちらは順序付き歌詞テキストを
  読む。`svprpe extract --lyrics` から `rpe/learned/lyrics_adapter.py` 経由で
  既存の Demucs vocals stem を転写し、`LearnedAudioAnnotations.
  lyrics_transcription` を populate する。出力側の検収計器
  （`svprpe lyrics-adherence`）は `eval/lyrics_match.py`（learned import なし）
  が担う。詳細は [`docs/lyrics_transcription_sensor.md`](lyrics_transcription_sensor.md)。
- License:
  - code: faster-whisper / ctranslate2 は MIT（`pip show` で確認済み、
    verified 2026-07-05）。
  - weights: `Systran/faster-whisper-small`（Hugging Face）— license: MIT
    （HF リポジトリの license バッジ "mit" を実確認、verified 2026-07-05。
    `openai/whisper-small`（MIT）の CTranslate2 変換で上流も MIT）。
    provenance 記録のスコープ（`lyrics_adapter._resolve_weights_identifier`
    の 3 段解決）: ① `/` を含む repo id / ローカルパスは **verbatim 記録**
    （マッピング不参照）→ ② インストール済み `faster_whisper.utils._MODELS`
    （private API を防御的に参照・形状検証つき）で shorthand を実ダウンロード
    先 repo に解決（`turbo` / `distil-*` 等は Systran 外 repo）→ ③ 静的
    `Systran/faster-whisper-{size}` 規約にフォールバック。未解決の repo 名は
    捏造しない。ライセンス文言は**解決後の repo** から導出: 個別検証済みは
    small のみ、他の Systran repo はファミリーバッジ下の未検証メンバー、
    それ以外（Systran 外 / verbatim）は「ライセンス未確認・採取時に実確認」
    でライセンス主張なし。解決経路（verbatim / upstream mapping / static）も
    note に記録する。
- Constraints:
  - 重み（CTranslate2 変換済みモデル）は optional extra `lyrics` に限定する。
    デフォルトインストールは変えない。`lyrics` extra は demucs を同梱する —
    デフォルト経路が vocals 分離込みのため、`pip install -e ".[lyrics]"`
    だけで分離込みのデフォルト経路が単独で立つことが契約
    （`semantic-embed` が torch を明示同梱するのと同じ精神）。
  - デフォルトで Demucs vocals stem 分離を先に行う（`--lyrics-no-separate`
    でフルミックス直接転写に切り替え可能 — demucs 不要のランタイム
    opt-out）— フルミックスでの歌詞認識は精度が大幅に落ちるため。
  - 決定論は `temperature=0.0` + `condition_on_previous_text=False` による
    同一マシン契約（CLAP と同じ位置付け。CTranslate2 のカーネル選択は
    ハードウェア/ビルド依存でクロスマシンのビット完全性は保証しない）。
  - `LearnedLyricsTranscription` は `SemanticRPE` / `PhysicalRPE` /
    `SVPForGeneration.style_tags` に一切書き込まれない
    （`LearnedAudioAnnotations.lyrics_transcription` に隔離）。

### 3.2 Reject

#### `Essentia` / `essentia-tensorflow`

Reason: AGPL and several non-commercial model weights conflict with the
distribution policy of `svp_rpe`.

This supersedes the speculative Q4'-2 entry in `roadmap_goal1.md`.

#### `madmom`

Reason: not compatible with Python 3.11+; bundled models / data are
under non-commercial terms. We cannot ship it through the Q5-2
Dockerfile path.

#### `BeatNet`

Reason: depends on `madmom` and inherits its compatibility and license
issues.

### 3.3 Hold

#### `openl3` / `torchopenl3`

Reason: Python 3.11 compatibility, maintenance status, and weights
licensing all need re-verification before adoption.

#### `autochord`

Reason: package itself is MIT, but it depends on the NNLS-Chroma VAMP
plugin (GPL-2.0) and is unsupported on Windows. Not adoptable as is —
this revises the Q2-2 recommendation in `roadmap_goal1.md`.

#### `EfficientAT`

Reason: promising but requires a spike to confirm packaging,
dependencies, and weights license terms.

## 4. License Policy

- Only MIT, Apache-2.0, BSD-class, and equivalently permissive licenses
  are acceptable for new runtime dependencies.
- Model weights distributed by an upstream project must be inspected
  separately from the code license. A permissive code license does not
  imply permissive weights.
- Each learned-annotation output record MUST carry `source_model`
  (label-side) plus a matching `LearnedModelInfo` entry in
  `enabled_models` that pins `name`, `version`, `license`, and
  `weights_license`, so downstream consumers can audit provenance
  without reloading the model.

## 5. Optional Dependency Policy

All learned-model backends are gated behind opt-in `pyproject.toml`
extras:

| Extra            | Pulls in            |
|------------------|----------------------|
| `beat`           | `beat_this`         |
| `learned-tags`   | `panns_inference`   |
| `pitch`          | `basic-pitch`       |
| `semantic-embed` | `laion-clap`        |
| `lyrics`         | `faster-whisper` + `demucs` (デフォルト経路が vocals 分離込みのため) |

The default install MUST remain green without any of these extras. Each
backend module performs a guarded import and falls back gracefully (or
omits the corresponding annotations) when the extra is not installed.

## 6. Output Isolation

Learned annotations live in a single dedicated container, attached to
`RPEBundle` as a sibling field — not as a sub-field of `PhysicalRPE` or
`SemanticRPE`.

The concrete schema lives in `src/svp_rpe/rpe/models.py`. Sketch:

```python
class LearnedModelInfo(BaseModel):
    name: str
    version: str | None = None
    provider: str | None = None
    task: Literal["tagging", "beat_downbeat", "pitch", "embedding", "other"]
    license: str | None = None
    weights_license: str | None = None


class LearnedAudioLabel(BaseModel):
    label: str
    category: Literal["audioset", "mood", "genre", "instrument", "other"] = "other"
    confidence: float
    source_model: str
    # NOT named `evidence` — that field name belongs to SemanticLabel and
    # carries rule-derived epistemic weight. `notes` is free-form model
    # provenance hints with no equivalent guarantee.
    notes: list[str] = Field(default_factory=list)


class LearnedEmbedding(BaseModel):
    source_model: str
    vector: list[float]
    dimensions: int  # validated to equal len(vector)


class LearnedSemanticAxis(BaseModel):
    axis: str
    contrast_fit: float  # signed A/B contrast, read as grip — not a [0,1] confidence
    positive_probes: list[str] = Field(default_factory=list)
    negative_probes: list[str] = Field(default_factory=list)
    source_model: str


class LearnedSemanticSection(BaseModel):
    # per-section counterpart to LearnedSemanticAxis (the "emotional arc")
    section: str
    start_sec: float
    end_sec: float
    axes: list[LearnedSemanticAxis]


class LearnedAudioAnnotations(BaseModel):
    schema_version: str = "1.2"
    enabled_models: list[LearnedModelInfo] = Field(default_factory=list)
    labels: list[LearnedAudioLabel] = Field(default_factory=list)
    embedding: LearnedEmbedding | None = None
    semantic_axes: list[LearnedSemanticAxis] = Field(default_factory=list)
    semantic_axis_sections: list[LearnedSemanticSection] = Field(default_factory=list)
    time_events: list[LearnedTimeEvent] = Field(default_factory=list)  # beat/downbeat 等
    note_events: list[LearnedNoteEvent] = Field(default_factory=list)  # pitch/onset 等
    lyrics_transcription: LearnedLyricsTranscription | None = None  # 歌詞転写センサー出力
    inference_config: dict[str, Any] = Field(default_factory=dict)
    license_metadata: dict[str, str] = Field(default_factory=dict)
    estimation_disclaimer: str = (
        "learned_annotations are model-derived estimates, "
        "not ground-truth music quality labels"
    )


class RPEBundle(BaseModel):
    ...
    learned_annotations: LearnedAudioAnnotations | None = None
```

`schema_version` is now `"1.2"` (bumped from `"1.1"` when
`lyrics_transcription` was added — see
[`docs/lyrics_transcription_sensor.md`](lyrics_transcription_sensor.md) —
which itself bumped from `"1.0"` when `semantic_axes` was added, see
[`docs/semantic_sensor_clap.md`](semantic_sensor_clap.md)). Each bump is a
default-`None`/empty field addition, so older payloads still validate.

Required metadata on every learned-annotation payload:

- `schema_version`
- `enabled_models[].name` / `.version` — which adapters were actually
  invoked for this run, with their model versions
- `labels[].source_model` — label-side back-reference; the matching
  version lives on the corresponding `enabled_models` entry
- `labels[].category` — one of `audioset` / `mood` / `genre` /
  `instrument` / `other` (intentionally a closed Literal; new
  categories require a schema PR and a docs update)
- `inference_config` — model-specific knobs that affected the output
- `license_metadata`
- `estimation_disclaimer` — a static string asserting that the contents
  are model estimates, not production-quality truth labels

`RPEBundle` uses an omit-None serializer for `learned_annotations`, so
bundles produced before the learned layer is populated keep their
pre-existing JSON shape (no `"learned_annotations": null` noise).

## 7. Promotion Gates (deterministic → learned 置換条件)

Learned output may be proposed for write-through into `PhysicalRPE` only in a
separate promotion PR, and only after all gates below are satisfied:

- **G1 (synth):** 5/5 synthetic songs show learned output beating the
  deterministic field by at least +0.05 F-measure.
- **G2 (synth tie-break):** learned mean absolute error is less than or equal
  to deterministic mean absolute error, so there is no precision regression.
- **G3 (real-audio):** a human-annotated real-audio dataset with at least 20
  songs shows learned win-rate >= 70% or average F-measure improvement >= +0.03.
- **G4 (license):** the model and model weights satisfy this policy's
  permissive-license requirements.
- **G5 (determinism):** identical input produces identical output; seeds are
  fixed where applicable and CPU/GPU differences are measured.

When only G1 and G2 are satisfied, learned output remains exposed through
`RPEBundle.learned_annotations` only. After G3 is satisfied, a later PR may
propose a `--prefer learned` path or explicit write-through behavior.

### Pseudo-label consensus is not a promotion gate

`scripts/build_pseudo_label_consensus.py` may compare deterministic RPE fields
against learned annotations on local real-audio files. That report is useful
for triage: it can identify tracks where independent machine paths agree or
disagree, and it can help prioritize human annotation work.

It does **not** satisfy G3. Machine-to-machine agreement is not human ground
truth, and pseudo-label consensus must not be used to write learned output into
`PhysicalRPE` / `SemanticRPE` or to claim real-audio accuracy.

## 8. Non-Goals

- Replacing librosa beat tracking, `pyin` melody extraction, or any
  current deterministic backend in the same change set that introduces
  the learned variant.
- Using learned tags to score or gate semantic repair decisions.
- Bundling pretrained weights inside this repository.

## 9. Implementation Order

The implementation is split into independent PRs so each step is small
and reviewable:

1. **PR1 — docs only** (this document; updates `roadmap_goal1.md`).
2. **PR2 — schema only.** Add `LearnedAudioAnnotations` and an optional
   field on `RPEBundle`. No backend code, no new runtime deps. Includes
   a serializer / backward-compatibility test.
3. **PR3 — `beat_this` backend spike.** Optional extra `beat`,
   `dbn=False`, fallback to the current librosa path, fake-backend
   tests.
4. **PR4 — `panns_inference` backend spike.** Optional extra
   `learned-tags`, top-k tags + optional embedding into
   `LearnedAudioAnnotations`. No write-through into `SemanticRPE`.
5. **PR5 — `basic-pitch` backend spike.** Optional extra `pitch`,
   note-event output, additive next to the existing `pyin` contour.
6. **PR6 — learned-output validation harness.** Compare `beat_this` and
   `basic-pitch` outputs against the synthetic ground truth without writing
   results back into `PhysicalRPE`.
7. **PR7 — pseudo-label consensus harness.** Compare deterministic real-audio
   measurements with optional learned annotations as machine consensus only;
   this does not satisfy the human-ground-truth promotion gate.
8. **PR2b-1 — CLAP isolated wiring + fixture-driven similarity harness.**
   Optional extra `semantic-embed`, `clap_adapter.py`
   (`embed_audio_file` / `embed_texts`), `similarity.py` (`cosine_similarity`
   / `prompt_audio_fit` / `contrast_fit`, numpy only), and the
   `scripts/collect_clap_fixture.py` runbook. No real inference in this PR —
   fake-backend tests only. First adapter to populate
   `LearnedAudioAnnotations.embedding`.
9. **PR2b-2 — CLAP real inference + real fixture collection + learned/rule
   grip cross-validation.** Final G4 license confirmation against the
   actually-fetched weights, real-audio fixture collection via
   `scripts/collect_clap_fixture.py`, and a cross-validation experiment
   against the rule-based `SemanticRPE` layer.
10. **PR-lyrics — faster-whisper symbolic lyrics-content sensor.** Optional
    extra `lyrics`, `lyrics_adapter.py` (`transcribe_lyrics`, isolated in
    `LearnedAudioAnnotations.lyrics_transcription`), `eval/lyrics_match.py`
    output-side instrument (`svprpe lyrics-adherence`, no learned imports).
    Fake-backend tests only; real inference / real-audio fixtures pending
    (see `docs/lyrics_transcription_sensor.md` §7).

## 10. Acceptance Criteria

A change set in this track is acceptable only if all of the following
hold:

- `Essentia`, `essentia-tensorflow`, `madmom`, and `BeatNet` are not
  introduced as direct or transitive runtime dependencies.
- Learned-model output is confined to `LearnedAudioAnnotations`.
- `PhysicalRPE` / `SemanticRPE` evidence layers are not modified to
  consume learned output.
- The default install (no optional extras) still passes the existing
  pipeline test suite.
- Every learned annotation record carries model name, model version,
  and license metadata.
- This document is updated whenever the adopt / reject / hold lists
  change.

## 11. Relationship To `roadmap_goal1.md`

- Q2-1 madmom dependency → replaced by `beat_this` (Adopt).
- Q2-2 `autochord` recommendation → moved to Hold pending GPL VAMP
  resolution.
- Q4' Essentia entry → Reject; the `LearnedAudioAnnotations` container
  is the new attachment surface for any future learned-model output.
- Q5-2 Dockerfile note about absorbing madmom build → no longer needed.
