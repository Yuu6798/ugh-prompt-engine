# Composition Score Product Brief

## 物理層×意味層レイヤー作曲のためのAI音楽中間表現

**Status**: CANONICAL — Composition Score 機能群の上位プロダクト定義  
**Created**: 2026-05-27  
**Downstream**: [`composition_poc_planning.md`](composition_poc_planning.md)（実装計画）  
**Upstream**: [`ai_music_daw_vision.md`](ai_music_daw_vision.md)（長期ビジョン）

---

## 1. 結論

Composition Score は、AI音楽生成のための新しい「楽譜」または「作曲用中間表現」である。

重要なのは、AIに音楽を「生成させる」のではなく、Composition Score を「演奏させる」という発想である。

従来のAI音楽生成は次の構造になりがちだった。

```text
人間が自然言語プロンプトを書く
→ AIが曲を生成する
→ 人間が聴いて選ぶ
```

Composition Score の構造は違う。

```text
人間がComposition Scoreを書く
→ AIがそれを演奏・解釈・レンダリングする
→ 人間がScoreを改訂する
```

この見方では、作品の本体は生成音源ではなく Composition Score 側にある。

```text
作品 = Composition Score
演奏 = AI生成音源
生成器 = 演奏者 / レンダラ
```

したがって、このプロダクトの中心は「監査」ではない。
中心は、物理層・意味層・構造層を使って曲を書くための作曲言語を成立させることである。

監査・RPE・ΔE比較は重要だが、それは作曲後のデバッグ、検証、改訂補助であり、プロダクトの主目的ではない。

---

## 2. プロダクトの一文定義

Composition Score は、AI音楽生成における「意味・構造・物理制約を持つ新しい楽譜」である。

または、

Composition Score は、自然言語プロンプトを、AIが演奏可能な作曲スコアへ進化させるための中間表現である。

よりプロダクト的に言うなら、

AI音楽生成を、バイブ指定からレイヤー作曲へ移行させる作曲フレームワークである。

---

## 3. 解決する問題

現状のAI音楽生成は、自然言語プロンプトへの依存が強い。

例:

```text
夜っぽくて、エモくて、サビが盛り上がる曲
```

このようなプロンプトは直感的だが、次の問題を持つ。

- 作曲意図が曖昧
- 物理的な音響条件が固定されない
- セクション構造が弱い
- 生成結果の差分が分解できない
- 何を修正すべきか分からない
- 同じ曲想を再利用・変奏しづらい
- 生成AIが「作曲家」扱いになり、作者性がAI側に寄りやすい

Composition Score はこれを次のように変える。

```text
作曲意図をScoreとして書く
→ Scoreから生成器向けプロンプトをレンダリングする
→ AIがScoreを演奏する
→ 必要ならRPEで読み戻す
→ Scoreを改訂する
```

この構造により、作曲者は「AIに丸投げする人」ではなく、「AIが演奏できるスコアを書く人」になる。

---

## 4. 従来の楽譜との差分

従来の楽譜は、主に人間演奏者に向けた演奏指示である。

```text
どの音を
いつ
どの長さで
どの楽器が
どう演奏するか
```

Composition Score は、AI生成器に向けた作曲・生成指示である。

```text
どんな意味核を持つ曲か
どんな構造的重力を持つか
どこでエネルギーを変化させるか
どんな物理状態を満たすべきか
どこまで変奏を許すか
```

差分を表にすると次の通り。

| 観点 | 従来の楽譜 | Composition Score |
|---|---|---|
| 主な読者 | 人間演奏者、指揮者、編曲者 | AI生成器、Prompt Renderer、人間作曲者 |
| 記述対象 | 音高、リズム、拍子、強弱、奏法 | 意味層、物理層、構造層、制約、許容ズレ |
| 単位 | 音符、小節、パート | セクション、エネルギー曲線、PoR、grv、RPE目標 |
| 作品の所在 | 楽譜 | Composition Score |
| 出力 | 演奏 | 生成音源 |
| 解釈の余地 | 演奏表現に残る | 生成器の確率的解釈に残る |
| 評価 | 楽譜通り演奏されたか | Scoreの意味・構造・物理条件を満たしたか |
| 変奏 | 編曲、演奏解釈、テンポ変更 | 意味層固定、物理層変更、構造層変更、レンダラ変更 |
| ゴール | 作品を再演する | 作品を生成・変奏・比較・改訂可能にする |

一番重要な差分は次である。

```text
従来の楽譜:
  何の音を鳴らすかを書く

Composition Score:
  どんな意味と物理状態を持つ曲として成立させるかを書く
```

Composition Score は五線譜の代替ではなく、AI生成器に向けた上位スコアである。
必要なら下位にMIDIや従来楽譜を持てる。

```text
Composition Score
  ↓
MIDI / traditional score / prompt
  ↓
AI生成器
  ↓
audio
```

---

## 5. 中心概念

### 5.1 物理層

物理層は、音響的・測定可能な条件である。

例:

```yaml
physical:
  bpm: 128
  key: "C minor"
  time_signature: "4/4"
  active_rate_target: "0.90-0.93"
  valley_depth_target: "0.15-0.25"
  rms_target: "model-dependent"
  crest_factor: "4.5-5.5"
  brightness: "dark"
  stereo_width: "wide"
  density_curve: "ascending"
```

物理層に含み得るもの:

- BPM
- Key / mode
- time signature
- duration
- Active Rate
- valley_depth
- RMS / loudness
- Crest Factor
- Thickness
- spectral balance
- stereo width / correlation
- density curve
- section-level dynamics

ここで重要なのは、これらを「評価指標」としてだけではなく、作曲上の制御パラメータとして使うことである。

### 5.2 意味層

意味層は、曲が何を感じさせるか、何を保つべきかを記述する。

例:

```yaml
semantic:
  core: "introspective night drive"
  grv:
    primary: "deep_house"
    secondary: "ambient"
  delta_e:
    overall: "gradual build from solitude to release"
  avoid:
    - "bright festival EDM"
    - "comic vocal delivery"
```

意味層に含み得るもの:

- PoR_core
- PoR_surface
- grv_anchor
- delta_e_profile
- mood / theme
- narrative direction
- avoid / forbid
- preserve
- flexible / variable elements

意味層は、従来プロンプトの「雰囲気」を構造化したものだが、単なる形容詞列ではない。
曲の重力、変化、保持すべき意味核を定義する。

### 5.3 構造層

物理層だけでは低すぎ、意味層だけでは高すぎる。
その間に必要なのが構造層である。

例:

```yaml
structure:
  - section: intro
    bars: 8
    role: "establish loneliness"
    physical: "low density, sub bass only"

  - section: verse
    bars: 16
    role: "restrained movement"
    physical: "sparse drums, short phrases, clear rests"

  - section: chorus
    bars: 16
    role: "emotional release"
    physical: "full energy, wide stereo, focused layers"

  - section: bridge
    bars: 8
    role: "near silence and reflection"
    physical: "no kick, no bass, minimal texture"
```

構造層に含み得るもの:

- intro / verse / chorus / bridge / outro
- section role
- energy curve
- motif handling
- density changes
- instrument entry / withdrawal
- chorus lift
- bridge valley
- repetition / variation
- transition type

Composition Score が「作曲」として成立するには、この構造層が重要である。
意味層だけならプロンプトであり、物理層だけなら音響スペックである。
構造層が入ることで、作曲言語になる。

---

## 6. 中心モデル

Composition Score の基本構造は次の通り。

```yaml
meta:
  title: "Midnight Signal"
  version: 0.1

semantic:
  core: "introspective night drive"
  grv:
    primary: "deep_house"
    secondary: "ambient"
  delta_e:
    overall: "gradual build from solitude to release"
  avoid:
    - "bright festival EDM"
    - "comic vocal delivery"

physical:
  bpm: 128
  key: "C minor"
  time_signature: "4/4"
  active_rate_target: "0.90-0.93"
  valley_depth_target: "0.15-0.25"
  brightness: "dark"
  stereo_width: "wide"

structure:
  - section: intro
    bars: 8
    role: "establish loneliness"
    physical: "low density, sub bass only"

  - section: verse
    bars: 16
    role: "restrained movement"
    physical: "sparse drums, short phrases, clear rests"

  - section: chorus
    bars: 16
    role: "emotional release"
    physical: "full energy, wide stereo, focused layers"

  - section: bridge
    bars: 8
    role: "near silence and reflection"
    physical: "no kick, no bass, minimal texture"

rendering:
  target_backend: "external"
  prompt_max_chars: 500
  priority:
    - semantic.core
    - semantic.grv
    - physical.bpm
    - physical.key
    - structure
    - physical.optional
```

このYAMLは単なる設定ファイルではない。
曲を書くためのスコアである。

---

## 7. 重要な思想転換: AIは作曲家ではなく演奏者

このプロダクトの最も強い視点は、AIを「作曲家」ではなく「演奏者 / レンダラ」と見ること。

従来:

```text
Prompt = 指示
AI = 作曲家
Audio = 作品
```

Composition Score:

```text
Composition Score = 作品 / 楽譜
AI = 演奏者 / レンダラ
Audio = 演奏結果
```

この見方にすると、AIの確率性は欠点ではなく、演奏差として扱える。

同じ楽譜でも演奏者が変われば解釈が変わる。
同じComposition Scoreでも、Suno、Udio、MusicGen、MIDIなどで違う出力が出る。
これは失敗ではなく、レンダラ差、演奏解釈差である。

この構造により、作者性はScore側に戻る。
AIが勝手に作った曲ではなく、人間が設計したScoreをAIが演奏した曲になる。

---

## 8. 監査の位置づけ

監査は主役ではない。

正しい関係は次の通り。

```text
Composition Score = 作曲
Prompt Renderer = 編曲指示 / 生成指示
Generator = 演奏者
RPE/ΔE Audit = 試聴後の構造的フィードバック
RepairSVP = 次の作曲改訂案
```

コードに例えるなら、

```text
ソースコードを書くこと = Composition Scoreを書くこと
コンパイルすること = Prompt/Audioにレンダリングすること
テストすること = RPE/ΔE audit
修正すること = Score改訂
```

監査は、作曲言語が実際の生成器でどれだけ実行されたかを見るための補助機構である。
PoCの第一目標は監査CIではなく、Composition Scoreが「曲を書くための言語」として成立することである。

---

## 9. プロダクトコンポーネント

### 9.1 Composition Score YAML

作曲者が書くスコア本体。

責務:

- 意味層を書く
- 物理層を書く
- 構造層を書く
- 制約を書く
- レンダリング優先度を書く

### 9.2 Prompt Renderer

Composition Score を各生成器向けプロンプトへ変換する。

対応候補:

- ExternalPromptAdapter: Suno / Udio向け
- MusicGenAdapter: MusicGen向け
- MidiAdapter: MIDI / FluidSynth向け

ただしMVPでは ExternalPromptAdapter のみでよい。

Prompt Renderer は単なる要約器ではない。
Score内の層構造を、生成器が読める形に変換するレンダラである。

例:

```text
128 BPM C minor deep house / ambient track.
Dark, introspective night-drive atmosphere.
Start with sparse sub bass and distant pads.
Gradually increase density toward a wide, emotional chorus.
Bridge should be near-silent with no kick and no bass.
Avoid bright festival EDM or comic vocal delivery.
```

### 9.3 Layer Manipulator

層ごとの変奏を作るための機能。

例:

```text
意味層固定 / 物理層変更
物理層固定 / 意味層変更
構造層固定 / 音色変更
Score v1 → v2 → v3
```

この機能により、Composition Score は単なるプロンプト生成器ではなく、作曲ツールになる。

### 9.4 RPE Feedback

生成された音源をRPEで読み戻し、Scoreとの差分を確認する補助機能。

ただしMVPの主役ではない。

---

## 10. PoCレベル別ロードマップ

### PoC 1: Layered Composition Score

目的:

物理層・意味層・構造層を分けて、1曲の作曲スコアとして書けることを示す。

成果物:

```text
composition_score.yaml
generated_prompt.txt
```

成功条件:

```text
Scoreを読むと曲の方向性が分かる
意味層と物理層が分離されている
構造層が曲の展開を定義している
生成用プロンプトに変換できる
```

監査は不要でもよい。

### PoC 2: Layer-to-Prompt Composition

目的:

Composition Score を生成器向けプロンプトに決定論的に変換する。

実装:

```text
src/svp_rpe/compose/models.py
src/svp_rpe/compose/loader.py
src/svp_rpe/compose/prompt_renderer.py
svprpe compose score.yaml
```

成功条件:

```text
同じScoreから同じPromptが出る
physical layer がテンポ・キー・密度・音色指示へ変換される
semantic layer がムード・主題・grvへ変換される
structure が展開指示へ変換される
```

### PoC 3: Layer Manipulation

目的:

層を操作することで、曲想の差分を作れることを示す。

例:

```text
A: semantic固定 / physical変更
B: physical固定 / semantic変更
C: structure固定 / surface変更
```

成果物:

```text
score_variants/
  semantic_fixed_physical_a.yaml
  semantic_fixed_physical_b.yaml
  physical_fixed_semantic_a.yaml
  physical_fixed_semantic_b.yaml
```

成功条件:

```text
層ごとの変更がプロンプト差分として明示される
作曲上の意図が保たれる
Scoreの再利用性が見える
```

### PoC 4: Renderer as Performer

目的:

同じComposition Scoreを複数レンダラに演奏させる。

対象:

```text
Suno / Udio = 外部高品質レンダラ
MusicGen = ローカル生成レンダラ
MIDI = 決定論的物理層レンダラ
```

成功条件:

```text
同一Scoreから複数の演奏結果が得られる
生成器ごとの差分を「演奏解釈」として説明できる
Scoreが作品本体として扱える
```

### PoC 5: RPE Feedback / Audit

目的:

生成音源をRPEで読み戻し、Score改訂に使う。

実装:

```text
RPEBundle → ObservedRPE adapter
svprpe audit score.yaml generated.wav
```

成功条件:

```text
Scoreと生成音源の差分が見える
RepairSVP / RepairScoreが次の改訂案として使える
```

ここで初めて監査が主役になる。

---

## 11. MVP実装範囲

最初のMVPでは、以下に限定する。

### 必須

```text
CompositionScore Pydantic model
YAML loader
composition_to_target_svp()
ExternalPromptAdapter
svprpe compose
examples/composition/midnight_signal/composition_score.yaml
```

### 後回し

```text
MusicGenAdapter
MidiAdapter
svprpe audit
RPEBundle → ObservedRPE adapter
section-aware audit
automatic repair loop
human validation
Layer Manipulator
```

理由:

最初に証明すべきことは、監査ではなく、Composition Score が作曲言語として読めて、生成プロンプトに変換できることである。

---

## 12. 実装ファイル（実装済み構成）

```text
src/svp_rpe/compose/
  __init__.py
  models.py
  loader.py
  convert.py
  fixity.py
  prompt_renderer.py

examples/composition/
  midnight_signal/
    composition_score.yaml
    generated_prompt.txt
    generated_prompt.json
    e2e/

tests/
  test_compose_schema.py
  test_compose_prompt_renderer.py
  test_composition_e2e.py
```

CLI:

```bash
svprpe compose examples/composition/midnight_signal/composition_score.yaml

svprpe compose examples/composition/midnight_signal/composition_score.yaml \
  --output generated_prompt.txt

svprpe compose examples/composition/midnight_signal/composition_score.yaml \
  --format json
```

出力例:

```json
{
  "backend": "external",
  "text": "128 BPM C minor deep house / ambient track...",
  "tags": ["deep_house", "ambient", "dark", "wide_stereo"],
  "negative_tags": ["bright festival EDM", "comic vocal delivery"],
  "dropped_elements": []
}
```

---

## 13. 設計判断ログ

### D1. TargetSVPを直接拡張しない

CompositionScoreを新設し、内部でTargetSVPへ変換する。

理由:

- TargetSVPはsemantic_ci内部モデル
- CompositionScoreは作曲者向けUX
- セクション構造や物理層制約はTargetSVPより上位概念
- 既存テストを壊さない
- スキーマを自由に進化させられる

### D2. 最初は監査を主役にしない

監査は重要だが、PoC 1の主役ではない。

理由:

- プロダクトの核は作曲言語
- 監査に寄せすぎると「AI音楽評価ツール」に見えてしまう
- 目指すのは「AI音楽のための新しい楽譜」

### D3. 最初はExternalPromptAdapterだけでよい

理由:

- Suno/Udioなど実際の高品質生成器へ接続できる
- MusicGen/MIDIは後続フェーズでよい
- 最短で「Scoreから曲を作る」体験を示せる

### D4. 構造層を必ず入れる

理由:

- 物理層だけでは音響スペック
- 意味層だけではプロンプト
- 構造層が入って初めて作曲になる

### D5. AIを作曲家ではなく演奏者として扱う

理由:

- 作者性がScore側に戻る
- 生成器の確率性を演奏差として扱える
- 同じScoreを複数レンダラで演奏できる
- Composition Scoreが作品本体になる

---

## 14. Claude Code / Codex への実装指示要約

最初に実装すべきものは、監査ではなく `svprpe compose` である。

### 実装タスク

1. `src/svp_rpe/compose/models.py`
   - `CompositionScore`
   - `Meta`
   - `SemanticLayer`
   - `PhysicalLayer`
   - `StructureSection`
   - `RenderingConfig`
   - `GeneratedPrompt`

2. `src/svp_rpe/compose/loader.py`
   - YAML読み込み
   - Pydantic validation

3. `src/svp_rpe/compose/convert.py`
   - `composition_to_target_svp(score: CompositionScore) -> TargetSVP`
   - 既存semantic_ciとの将来接続用

4. `src/svp_rpe/compose/prompt_renderer.py`
   - `ExternalPromptAdapter`
   - deterministic rendering
   - max_chars compression
   - dropped_elements tracking

5. `src/svp_rpe/cli.py`
   - `svprpe compose score.yaml`
   - `--output`
   - `--format text|json`
   - `--max-chars`

6. examples
   - `examples/composition/midnight_signal/composition_score.yaml`
   - `examples/composition/midnight_signal/generated_prompt.txt`

7. tests
   - YAML load test
   - prompt rendering snapshot test
   - dropped_elements test
   - target_svp conversion test

### 非目標

最初のPRでは以下をやらない。

```text
MusicGen統合
MIDI生成
音源監査
自動repair loop
section-aware RPE
人間評価
Layer Manipulator
```

---

## 15. 最終ゴール

PoCレベルの最終ゴール:

```text
1曲をComposition Scoreで設計し、
そのScoreから生成可能なプロンプトを作り、
実際に生成された曲が
「物理層×意味層×構造層の作曲」として説明可能になること。
```

研究・プロダクトとしての最終ゴール:

```text
Composition Scoreを、
AI音楽のための新しい楽譜、
つまりAI作曲用の中間表現として確立すること。
```

プロダクトの旗印:

```text
AIに音楽を生成させるのではなく、演奏させる。
そのための楽譜が Composition Score である。
```
