# VoiceGenesis Artificial Founder Series — AF-P0
## Procedural Artificial Voice Founder Generation, UTAU Embodiment, Founder-Trait Preservation, and Ground-Truth Recovery PoC
### Claude / Codex 実装専用設計・実行契約 v1.1

**策定日:** 2026-08-22  
**対象リポジトリ:** `Yuu6798/ugh-prompt-engine`  
**設計基準:** `main @ 2a8f69b155407c5fb5f57d4458ba202aa9bd9f1b`  
**新規実装先:** `voice_genesis/foundry/artificial_founder/`  
**系列名:** Artificial Founder Series  
**実験ID:** `AF-P0`  
**始祖ID:** `AF0`  
**AF0 phenotype codename:** `Dual Resonance / Afterglow`  
**実行資源:** CPUのみ。GPU学習、外部録音、耳判定はP0必須条件ではない。  
**位置づけ:** Voice Genome S系列終了後の独立研究系列。S2 / S3 / S3.5 / S4 / S4b の判定は変更しない。  
**成功時:** `Artificial Founder AF0 ESTABLISHED` を宣言し、P0を凍結する。  
**本書の状態:** 事前登録設計。結果を見てAF0特徴、候補、許容値、Gateを都合よく変更してはならない。  
**v1.1変更点:** v1.0の無個性なAF0を廃し、AF0固有の始祖形質をSource / Identity / Founder Expressionの3層に正式定義した。

---

# 0. 研究上の位置づけ

Voice Genome S系列は、実在する声から逆向きに構造を推定した。

```text
Human-derived voice
        ↓
Identity / Performance separation
        ↓
F0 / Duration / Energy / Release_partial
```

Artificial Founder Seriesは方向を反転する。

```text
Known founder specification
        ↓
Procedural artificial phonation
        ↓
UTAU-compatible body
        ↓
VoiceGenesis ingestion
        ↓
Re-expression
        ↓
Ground-truth recovery
```

AF-P0が答える中心質問:

> **人間の録音、既存Voicebank、学習済み音声モデルを始祖素材に使わず、既知の数値仕様からAF0という人工始祖の身体を生成し、VoiceGenesisへ取り込み、通常の声形質だけでなくAF0固有の「始祖形質」を再回収できるか。**

P0は「人間にどれだけ似たか」を目的にしない。

```text
Voice-like           = required
Human-like           = not a target
Human-origin         = prohibited
AF0-origin traceable = required
```

---

# 1. AF0の設計思想

AF0は単なる測定用test toneではなく、最初から固有の声質を持つ。

ただしP0では奇抜さそのものを成功条件にしない。

狙う位置:

```text
Human speech/singing ---------------- AF0 ---------------- Sound effect
自然な人声                         人工歌手                  効果音
```

AF0は「声として識別可能だが、出自が人工であることを隠さない」領域に置く。

AF0の特徴は3階層に分ける。

```text
AF0
├─ SOURCE
│   └─ Harmonic Lattice
│
├─ IDENTITY
│   └─ Dual Ancestral Resonance
│
├─ PERFORMANCE
│   └─ stable / regular baseline
│
└─ FOUNDER EXPRESSION
    └─ Afterglow Release
```

この分離は将来のP1 Mutation / P2 Crossoverで、どの階層が継承・変異したかを追えるようにするためである。

---

# 2. AF0 — Frozen Phenotype v0.1

```text
AF0 — Artificial Founder

SOURCE
  fundamental: harmonic procedural source
  odd/even harmonic ratio: 1.00 : 0.72
  jitter: 0 cent
  stochastic breath/noise: minimal and deterministic

IDENTITY
  normal vowel F1/F2/F3 scaffold
  AR-α: 3400 Hz
  AR-β: 5100 Hz
  β/α target energy ratio: 0.35

PERFORMANCE
  F0: stable
  vibrato: none
  Duration target for /r+i/: 20/80
  Energy: highly regular
  Release: 120 ms

FOUNDER EXPRESSION
  terminal F0 fall: -100 cent
  AR-α resonance afterglow: +35 ms
```

P0 canonical runではこの数値を変更しない。

---

# 3. AF0始祖形質の正式定義

## 3.1 Founder Source Trait — Harmonic Lattice HL-α

AF0の有声源は整数倍音を持つ決定論的harmonic sourceとする。

基本振幅:

```text
A(h) = 1 / h^p
p = 1.15
```

奇数・偶数倍音へ追加係数:

```text
odd harmonic multiplier  = 1.00
even harmonic multiplier = 0.72
```

したがって、

```text
HL-α = odd/even source weighting = 1.00 : 0.72
```

これは単なるhidden watermarkではない。

実際のスペクトル源を構成するFounder Source Traitである。

P0ではHL-αの「知覚できるか」は判定しない。

測定可能性と再発現を判定する。

---

## 3.2 Founder Identity Trait — Dual Ancestral Resonance AR-α / AR-β

通常母音のF1/F2/F3とは別に、AF0の全有声音へ共通する固定共鳴を持たせる。

```text
AR-α
  center = 3400 Hz
  bandwidth = 220 Hz
  nominal gain = +6.0 dB

AR-β
  center = 5100 Hz
  bandwidth = 300 Hz
  relative target:
    β/α energy ratio = 0.35
```

AR-αを主始祖マーカーとする。

AR-βは補助マーカー。

重要:

```text
AR-α / AR-β
≠ metadata watermark
≠ inaudible ID
```

実際の声質形成フィルタである。

将来の系譜では:

```text
AR-α present
AR-α shifted
AR-α weakened
AR-α lost
```

をFounder-lineage traitとして追跡できる。

---

## 3.3 Founder Expression Trait — Afterglow Release AG-α

AF0の終端は単なる120 ms振幅taperではない。

```text
main voiced body
      ↓
terminal F0 fall = -100 cent
      ↓
main amplitude half-cosine taper = 120 ms
      ↓
AR-α resonance remains +35 ms
      ↓
digital zero
```

AG-α:

```text
main_release_ms             = 120
terminal_f0_delta_cents     = -100
ar_alpha_afterglow_extra_ms = 35
```

これはS系列のReleaseGeneと同一概念ではない。

P0では:

```text
Founder Expression Trait
```

として別記録する。

P1以降で遺伝対象に昇格するかは別裁定。

---

# 4. P0の成功主張の上限

AF-P0 PASS時に言ってよいこと:

> **Source-Freeなprocedural specificationから、AF0固有のHarmonic Lattice、Dual Ancestral Resonance、Afterglow Releaseを持つUTAU形式人工始祖を決定論生成し、VoiceGenesisへ取り込み、Identity / PerformanceおよびFounder Trait群を事前登録許容内で再回収した。**

短縮宣言:

```text
Artificial Founder AF0 ESTABLISHED
Founder Trait Set AF0/0.1 PRESERVED
```

AF-P0 PASSでも言ってはいけない:

```text
× 人間のように自然
× 完成した日本語歌手
× 人工生命が成立
× geneが教育可能
× 世代遺伝が成立
× crossover成立
× Founder Traitが知覚的に顕著
× HL-α / AR-α / AG-αが進化的に有利
× AF0に人格や意識がある
```

---

# 5. 非目的 — P0で行わないこと

```text
× 人間音声の録音
× Ritsu / PJS / Amitaro / User音源の利用
× 初音ミク等の商用Voicebank利用
× 既存UTAU Voicebankを始祖として利用
× TTS / VC / singing modelでAF0を生成
× speaker embedding
× DiffSinger学習
× GPU
× 自然さをPASS条件にする
× 手作業oto.ini調整
× WAV手修正
× 結果を聴いてAR-α等を変更
× AF0結果を見て許容値を変更
× P1 mutation
× P2 crossover
× Human × Synthetic
× S系列の再裁定
```

---

# 6. 用語

## Artificial Founder

明示的数値仕様と決定論的アルゴリズムから生成された人工Voice始祖。

## Founder Genome

唯一の正典入力:

```text
founder_specs/AF0.json
```

## Founder Body

Genomeからコンパイルされた:

```text
WAV
oto.ini
character.txt
```

## Founder Trait

AF0固有の出自追跡可能な声質・発声特性。

P0:

```text
HL-α
AR-α
AR-β
AG-α
```

## Ground Truth

AF0.jsonに定義した設計値。

## Body Realization

44.1 kHz PCM16のUTAU Voicebank WAV上の実現。

## VoiceGenesis Re-expression

既存UTAU donor経路でWORLD分析し24 kHzで再表現した出力。

---

# 7. 全体アーキテクチャ

```text
AF0.json
criteria.json
controls.json
probes.json
      │
      ▼
Founder Compiler
      │
      ├─ harmonic source
      ├─ HL-α
      ├─ vowel F1/F2/F3
      ├─ AR-α / AR-β
      ├─ performance envelope
      └─ AG-α
      │
      ▼
AF0 UTAU Body
      │
      ├──────────────┐
      ▼              ▼
Body Measurement   UTAU Validation
                     │
                     ▼
              VoiceGenesis Ingestion
                     │
                     ▼
               WORLD Re-expression
                     │
                     ▼
              Re-expression Measure
                     │
                     ▼
                  Compare
                     │
                     ▼
PASS / NOT_ESTABLISHED / BLOCKED / FAILED
```

---

# 8. AF0 Founder Genome v0.2 schema

```json
{
  "schema": "voicegenesis-artificial-founder/0.2",
  "founder_id": "AF0",
  "phenotype_codename": "Dual Resonance / Afterglow",

  "origin": {
    "kind": "procedural_source_filter",
    "human_audio_used": false,
    "speaker_specific_parameters_used": false,
    "pretrained_voice_model_used": false,
    "external_voicebank_used": false,
    "generic_phonetic_topology_used": true
  },

  "generator": {
    "sample_rate_hz": 44100,
    "channels": 1,
    "pcm_format": "s16le",
    "canonical_pitch_name": "C4",
    "canonical_pitch_hz": 261.625565,
    "harmonic_count": 40,
    "harmonic_rolloff_power": 1.15,
    "global_seed": 0
  },

  "founder_source_traits": {
    "HL-alpha": {
      "kind": "odd_even_harmonic_lattice",
      "odd_multiplier": 1.0,
      "even_multiplier": 0.72,
      "scope": "all_voiced_source"
    }
  },

  "identity_signature": {
    "vowels": {
      "a": [720.0, 1350.0, 2600.0],
      "i": [280.0, 2450.0, 3300.0],
      "u": [340.0, 950.0, 2500.0],
      "e": [500.0, 2050.0, 2850.0],
      "o": [500.0, 820.0, 2500.0]
    },
    "vowel_formant_bandwidths_hz": [70.0, 95.0, 140.0],

    "founder_resonances": {
      "AR-alpha": {
        "center_hz": 3400.0,
        "bandwidth_hz": 220.0,
        "gain_db": 6.0
      },
      "AR-beta": {
        "center_hz": 5100.0,
        "bandwidth_hz": 300.0,
        "beta_alpha_energy_ratio": 0.35
      }
    }
  },

  "performance_genes": {
    "f0": {
      "core_f0_hz": 261.625565,
      "vibrato_depth_cents": 0.0,
      "jitter_cents": 0.0
    },
    "duration": {
      "target_sequence": ["r", "i"],
      "articulation_ms": 200.0,
      "r_onset_ms": 40.0,
      "i_vowel_ms": 160.0,
      "r_onset_share": 0.20
    },
    "energy": {
      "attack_ms": 30.0,
      "sustain_dbfs": -12.0,
      "sustain_ripple_db": 0.0
    },
    "release": {
      "main_taper_ms": 120.0,
      "curve": "half_cosine"
    }
  },

  "founder_expression_traits": {
    "AG-alpha": {
      "terminal_window_ms": 80.0,
      "terminal_f0_delta_cents": -100.0,
      "ar_alpha_afterglow_extra_ms": 35.0,
      "terminal_zero_hold_ms": 80.0
    }
  },

  "body": {
    "format": "utau-classic-cv",
    "pitch_dirs": ["C4"],
    "lead_zero_ms": 20.0,
    "tail_zero_ms": 80.0
  },

  "inventory": {
    "aliases": [
      "あ", "い", "う", "え", "お",
      "か", "き", "く", "け", "こ",
      "さ", "し", "す", "せ", "そ",
      "な", "に", "ぬ", "ね", "の",
      "ら", "り", "る", "れ", "ろ"
    ]
  }
}
```

---

# 9. 人工音声生成方式

## 9.1 有声source

```text
source(t)
=
Σ h=1..40 [
  base_amp(h)
  × lattice_multiplier(h)
  × sin(phase_h(t))
]
```

```text
base_amp(h) = 1 / h^1.15
```

```text
lattice_multiplier:
  odd  = 1.00
  even = 0.72
```

F0は時間積分した位相から生成し、terminal windowのみ-100 centへ滑らかに下降。

jitter = 0。

vibrato = 0。

## 9.2 deterministic noise

`s`やburst用noiseは固定整数PRNG。

推奨:

```text
xorshift64*
```

unit別seed:

```text
SHA256(founder_id + "/" + alias + "/" + component)
```

から決める。

OS entropy禁止。

## 9.3 vowel filter

母音ごとにF1/F2/F3共鳴器。

その後に共通Founder Identity filter:

```text
AR-α
AR-β
```

を通す。

重要:

ARはF1/F2/F3と同一フィルタに潰さず、別stageとして実装する。

理由:

```text
normal vowel identity
vs
founder identity
```

を測定・mutation時に分離できるようにするため。

## 9.4 consonant

P0では「自然な日本語」を最適化しない。

最低実装:

```text
k:
  closure + deterministic burst

s/sh:
  deterministic high-band noise

n:
  voiced source + nasal shaping

r:
  short tap/interruption + voiced transition

none:
  vowel only
```

目的:

```text
phonetic usability
```

であり、native-likenessではない。

---

# 10. Afterglow Release実装

AG-αは通常releaseと別stageにする。

```text
voice source / vowel filters
          │
          ├─ normal path
          │    ↓
          │ 120 ms half-cosine taper
          │
          └─ AR-α resonant path
               ↓
             120 ms main taper
               +
              35 ms residual decay
```

重要:

「AR-αだけを35 ms長く残す」。

全帯域を35 ms延長してはいけない。

そうするとFounder Identityではなく単なる長いreleaseになる。

terminal F0 fallはmain sourceへ適用し、残光は共鳴器のdecayとして表現する。

---

# 11. UTAU Body

```text
AF0/
├── character.txt
├── readme.txt
├── founder_manifest.json
├── unit_truth.json
├── SHA256SUMS.txt
└── C4/
    ├── oto.ini
    ├── a.wav
    ├── i.wav
    ├── ...
    └── ri.wav
```

WAV filenameはASCII。

aliasはかな。

## oto.ini

```text
filename=alias,offset,consonant,blank,preutterance,overlap
```

自動生成のみ。

人手編集禁止。

母音:

```text
consonant = 0
preutterance = 0
overlap = 0
```

子音:

```text
offset       = lead_zero_ms
consonant    = onset duration
preutterance = onset duration
overlap      = min(15 ms, onset / 2)
blank        = positive tail-zero distance
```

---

# 12. VoiceGenesis Ingestion

既存経路をread-only利用:

```text
voice_genesis/foundry/adapter/donor_bank_utau.py
voice_genesis/foundry/adapter/donor_bank.py
voice_genesis/foundry/adapter/units.py
voice_genesis/foundry/adapter/joins.py
```

P0で既存Ritsu/PJSロジックを変更しない。

AF0全25 WAVを分析する。

Ritsu向けsubset optimizationをAF0へ適用しない。

```text
selected AF0 WAV = all
```

VoiceGenesis側で:

```text
WAV + oto
↓
WORLD
↓
f0 / sp / ap
↓
WORLD synthesize
↓
re-expression WAV
```

を実施する。

join smoke:

```text
か → り → あ
```

1ケース。

join smokeはtrait Gateではなくingestion viabilityのみ。

---

# 13. DiffSinger-compatible conversion

新規:

```text
convert_founder.py
```

出力:

```text
dataset/
├── transcriptions.csv
├── wavs/
├── provenance.json
└── dataset_stats.json
```

最低schema:

```text
name
ph_seq
ph_dur
```

`ph_seq` / `ph_dur`はFounder Genomeから決定論導出する。

音響推定でannotationを作らない。

DiffSinger学習はP0範囲外。

---

# 14. 測定器の分離

```text
af_synth.py
  ↓
WAV

af_measure.py
  ↓
measurements

af_compare.py
  ↓
truth comparison
```

`af_measure.py`はAF0期待値を読めない構造にする。

禁止:

```text
af_measure.py -> founder_specs/AF0.json
```

許可:

```text
af_compare.py -> AF0.json
```

---

# 15. 測定対象

## 15.1 Standard Identity

5母音:

```text
F1
F2
F3
```

## 15.2 Founder Source Trait HL-α

有声安定区間でsource-like harmonic envelopeを推定。

最低metric:

```text
odd_harmonic_energy
even_harmonic_energy
observed_even_odd_ratio
```

注意:

formantによる局所増幅の影響を避けるため、単一倍音1本で判定しない。

複数倍音をband-normalized aggregationする。

## 15.3 Founder Identity AR-α / AR-β

5母音ごとに:

```text
AR-alpha center
AR-alpha prominence
AR-beta center
beta/alpha relative energy
```

を測る。

AR-αがF3と近い母音でも、既知の局所band modelで別peakとして同定できなければ測定不能とする。

結果を見てpeak finderを変更しない。

## 15.4 F0

```text
core median F0
terminal delta cents
```

## 15.5 Duration

対象:

```text
ら り る れ ろ
```

測定:

```text
r onset duration
vowel duration
r_onset_share
```

oto.iniを読まずWAVから測る。

## 15.6 Energy

```text
attack 10–90%
sustain RMS dBFS
envelope correlation
```

## 15.7 Release

```text
main release duration
main curve correlation
```

## 15.8 Founder Expression AG-α

測定:

```text
terminal F0 fall
AR-alpha persistence relative to broadband/main release
```

定義:

```text
afterglow_extra_ms
=
AR-alpha disappearance time
-
main broadband disappearance time
```

これが+35 ms近傍かを見る。

---

# 16. 二段測定

```text
B = Body
  44.1 kHz AF0 Voicebank

V = VoiceGenesis
  WORLD re-expression 24 kHz
```

両方測る。

これで:

```text
compiler失敗
body表現失敗
WORLD分析/再合成で形質喪失
```

を分離する。

---

# 17. Meter Controls

AF0結果を見る前にcontrol fixtureで測定器を検証。

| Metric family | control low | control high |
|---|---:|---:|
| HL-α even multiplier | 0.45 | 1.00 |
| AR-α center | 3000 Hz | 3900 Hz |
| AR β/α ratio | 0.10 | 0.70 |
| terminal F0 delta | 0 cent | -200 cent |
| Duration r share | 0.10 | 0.40 |
| Energy sustain | -18 dBFS | -6 dBFS |
| Release | 40 ms | 200 ms |
| Afterglow | 0 ms | 80 ms |

control方向を正しく弁別できないmetric:

```text
METER_NOT_CALIBRATED
```

AF0を評価せずBLOCKED。

---

# 18. 事前登録許容値

## 18.1 Body

| Trait | Body Gate |
|---|---|
| vowel F1/F2/F3 | `max(40 Hz, 3%)`以内、15 peak中13以上 |
| HL-α even/odd ratio | target 0.72に対し `±0.08` |
| AR-α center | `±50 Hz` |
| AR-α detected context | 5母音中5 |
| AR-β center | `±90 Hz` |
| β/α energy ratio | `0.35 ± 0.08` |
| F0 core | `<= 5 cent` |
| terminal F0 delta | `<= 15 cent` error |
| Duration onset | `<= 2 ms` |
| r share | `<= 0.02` error |
| Energy sustain | `<= 0.5 dB` |
| Energy attack | `<= 5 ms` |
| Release | `<= 2 ms` |
| Afterglow extra | `35 ± 5 ms` |
| terminal digital zero | exact PCM zero |

## 18.2 VoiceGenesis re-expression

| Trait | Re-expression Gate |
|---|---|
| vowel spectral identity | envelope cosine each `>=0.85`, median `>=0.90` |
| HL-α even/odd ratio | Bodyとの差 `<= 0.15` |
| AR-α center | Bodyとの差 `<= 150 Hz` |
| AR-α contexts | 5母音中4以上 |
| AR-β center | Bodyとの差 `<= 220 Hz` |
| β/α ratio | Bodyとの差 `<=0.15` |
| F0 core | `<=25 cent` |
| terminal F0 delta | `<=50 cent` |
| Duration onset | `<=15 ms` |
| r share | `<=0.08` |
| Energy sustain | `<=2 dB` |
| Release | `<=30 ms` |
| Afterglow extra | Bodyとの差 `<=20 ms` |

AR-αは主Founder Identityなので、AR-βだけ通ってAR-αが落ちた場合はFounder Identity PASSにしない。

---

# 19. Gate

## G0 SOURCE_FREE

必須:

```text
human audio = 0
external voicebank = 0
pretrained voice model = 0
speaker embedding = 0
network access = 0
```

宣言だけでなくread-set tripwireで検証。

## G1 SPEC_VALID

schema、数値、formant、resonance、duration、aliasをfail-closed検査。

## G2 DETERMINISTIC_COMPILATION

same-process + independent-process:

```text
WAV
oto.ini
truth
manifest
dataset
```

SHA一致。

## G3 UTAU_BODY

```text
pitch dirs = 1
entries = 25
WAV = 25
missing = 0
orphan = 0
malformed = 0
```

## G4 VOICEGENESIS_INGESTION

DonorBank、WORLD、join smoke、dataset conversion。

## G5 METER_CONTROL

§17全controlが規定方向を弁別。

## G6 STANDARD_IDENTITY

F1/F2/F3 scaffold回収。

## G7 FOUNDER_SOURCE_HL

HL-α回収。

## G8 FOUNDER_IDENTITY_AR

AR-α必須、AR-β補助を回収。

## G9 F0

F0 core + terminal fall回収。

## G10 DURATION

20/80 trait回収。

## G11 ENERGY

regular Energy回収。

## G12 RELEASE

120 ms main Release回収。

## G13 FOUNDER_EXPRESSION_AG

Afterglow + terminal fallの複合形質を回収。

## G14 PROVENANCE_AND_PUBLICATION

pins、hash、atomic publish、rollback。

---

# 20. Overall Verdict

## PASS

```text
G0–G14 all PASS
```

宣言:

> **Artificial Founder AF0 ESTABLISHED — AF0固有のHarmonic Lattice HL-α、Dual Ancestral Resonance AR-α/β、Afterglow Expression AG-αを含む人工始祖がSource-Freeに生成され、VoiceGenesis再表現後も事前登録範囲で回収された。**

## NOT_ESTABLISHED

Source-Free、決定論、UTAU、VoiceGenesis ingestionは成立したが、1つ以上のtrait Gateが不成立。

詳細reason codeを残す。

例:

```text
FOUNDER_IDENTITY_NOT_ESTABLISHED
FOUNDER_SOURCE_NOT_ESTABLISHED
AFTERGLOW_NOT_ESTABLISHED
DURATION_NOT_ESTABLISHED
```

## BLOCKED

meter未校正、dependency欠落、generic adapter defectなどで判定不能。

## FAILED

Source-Free違反、決定論違反、provenance虚偽、partial publication。

---

# 21. 実装ディレクトリ

```text
voice_genesis/foundry/artificial_founder/
├── DESIGN_AF_P0.md
├── __init__.py
├── af_schema.py
├── af_spec.py
├── af_source.py
├── af_filter.py
├── af_expression.py
├── af_synth.py
├── af_utau.py
├── af_ingest.py
├── af_measure.py
├── af_compare.py
├── af_gates.py
├── af_report.py
├── p0_run.py
├── founder_specs/
│   └── AF0.json
├── criteria/
│   └── AF_P0_CRITERIA.json
├── controls/
│   └── AF_P0_CONTROLS.json
├── probes/
│   └── AF_P0_PROBES.json
├── tests/
│   └── test_artificial_founder_p0.py
└── results/
    └── .gitignore
```

---

# 22. モジュール責務

## af_source.py

```text
harmonic source
HL-α
fixed noise PRNG
F0 trajectory
```

## af_filter.py

```text
vowel F1/F2/F3
AR-α
AR-β
```

## af_expression.py

```text
Energy
main Release
terminal F0 fall
Afterglow
```

この分離を維持する。

将来:

```text
source mutation
identity crossover
expression mutation
```

を独立実装するため。

---

# 23. 実行順

```text
Phase 0
Preregister + pin

Phase 1
Meter control

Phase 2
Compile AF0

Phase 3
Independent recompute

Phase 4
UTAU structural validation

Phase 5
Body measurement

Phase 6
VoiceGenesis ingestion

Phase 7
Re-expression measurement

Phase 8
Ground Truth comparison

Phase 9
Publish verdict

STOP
```

P1へ自動進行しない。

---

# 24. 1コマンド実行

```bash
python -m voice_genesis.foundry.artificial_founder.p0_run \
  --spec voice_genesis/foundry/artificial_founder/founder_specs/AF0.json \
  --criteria voice_genesis/foundry/artificial_founder/criteria/AF_P0_CRITERIA.json \
  --controls voice_genesis/foundry/artificial_founder/controls/AF_P0_CONTROLS.json \
  --probes voice_genesis/foundry/artificial_founder/probes/AF_P0_PROBES.json \
  --out voice_genesis/foundry/artificial_founder/results/AF0
```

exit code:

```text
0 PASS
1 NOT_ESTABLISHED
3 BLOCKED
4 FAILED
```

---

# 25. 出力

```text
results/AF0/
├── p0_results.json
├── AF_P0_RECORD.md
├── founder_manifest.json
├── source_free_attestation.json
├── code_closure.json
├── input_pins.json
├── comparison.json
├── SHA256SUMS.txt
├── voicebank/
│   └── AF0/
├── dataset/
├── measurements/
│   ├── controls.json
│   ├── body.json
│   ├── reexpressed.json
│   └── founder_traits.json
├── probes/
│   ├── body/
│   └── reexpressed/
└── freeze/
    ├── ARTIFICIAL_FOUNDER_AF0_FREEZE.json
    └── ARTIFICIAL_FOUNDER_AF0_FREEZE.md
```

freezeはPASS時のみ。

---

# 26. p0_results.json

```json
{
  "schema": "voicegenesis-artificial-founder-p0/1.1",
  "founder_id": "AF0",
  "phenotype": "Dual Resonance / Afterglow",

  "source_free": {
    "verdict": "PASS"
  },

  "determinism": {
    "same_process": "PASS",
    "cross_process": "PASS"
  },

  "body": {
    "utau": "PASS",
    "voicegenesis_ingestion": "PASS"
  },

  "standard_traits": {
    "identity": {"verdict": "PASS"},
    "f0": {"verdict": "PASS"},
    "duration": {"verdict": "PASS"},
    "energy": {"verdict": "PASS"},
    "release": {"verdict": "PASS"}
  },

  "founder_traits": {
    "HL-alpha": {"verdict": "PASS"},
    "AR-alpha": {"verdict": "PASS"},
    "AR-beta": {"verdict": "PASS"},
    "AG-alpha": {"verdict": "PASS"}
  },

  "overall": {
    "verdict": "PASS"
  }
}
```

---

# 27. Determinism / Source-Free

固定:

```text
Python
numpy
scipy
pyworld
sample rate
float dtype
filter formula
PRNG
rounding
JSON canonicalization
file order
```

canonical generator read whitelist:

```text
AF0.json
criteria
controls
probes
source code
Python/package runtime files
```

禁止read:

```text
*.wav outside generated staging
*.mp3
existing voicebank dirs
speaker embedding
checkpoint
onnx voice model
```

---

# 28. Artifact Safety

全bundleをstagingで構築。

```text
build complete
↓
validate all
↓
hash all
↓
publish atomically
```

失敗時は旧valid bundleを保持。

partial generationをcanonical成果物として残さない。

---

# 29. テスト最低要件

## Schema / founder traits

```text
1 unknown key拒否
2 NaN/Inf拒否
3 invalid F1/F2/F3拒否
4 AR-alpha Nyquist違反拒否
5 AR-alpha bandwidth非正拒否
6 beta/alpha ratio範囲違反拒否
7 odd/even multiplier非正拒否
8 afterglow負値拒否
```

## Source

```text
9 fixed harmonic vector
10 HL-alpha odd/even reference
11 fixed PRNG vector
12 jitter=0 reference
13 terminal -100 cent trajectory
```

## Filter

```text
14 vowel peak reference
15 AR-alpha center reference
16 AR-beta center reference
17 beta/alpha relation
18 filter stability
```

## Expression

```text
19 attack reference
20 120ms taper reference
21 AR-alpha +35ms afterglow
22 final digital zero
```

## UTAU

```text
23 25 aliases
24 25 WAV
25 malformed oto拒否
26 missing WAV拒否
27 orphan WAV拒否
28 nested reference拒否
29 identity hash WAV差替え検知
30 identity hash oto差替え検知
```

## Determinism

```text
31 same-process SHA
32 cross-process SHA
33 manifest reproducibility
34 AF0 spec 1 field変更でhash変化
```

## Ingestion

```text
35 all vowel units
36 required onsets
37 WORLD finite
38 re-expression finite
39 join smoke
40 dataset conversion
```

## Meter

```text
41 HL control
42 AR-alpha control
43 AR ratio control
44 F0 control
45 Duration control
46 Energy control
47 Release control
48 Afterglow control
49 af_measure cannot read AF0.json
```

## Verdict

```text
50 AR-alpha fail → Founder Identity NOT_ESTABLISHED
51 HL fail → Founder Source NOT_ESTABLISHED
52 AG fail → Founder Expression NOT_ESTABLISHED
53 source-free violation → FAILED
54 determinism violation → FAILED
55 meter fail → BLOCKED
56 freeze only on PASS
```

## Regression / discipline

```text
57 existing donor_bank_utau normal flow
58 existing Ritsu tests
59 convert_ritsu unchanged
60 producer→verifier happy path
61 artifact rollback
```

---

# 30. User / Claude / Codex

## Codex

```text
implementation
tests
AF0 generation
UTAU build
measurement
VoiceGenesis ingestion
hash/provenance
```

## Claude

```text
design review
claim discipline
verdict audit
failure classification
```

## User

通常必要なのは:

```text
start approval
BLOCKED adjudication
P0 → P1 adjudication
```

P0実行中:

```text
recording      = none
hearing labels = none
oto tuning     = none
GPU operation  = none
manual upload  = none
```

---

# 31. P0後

## AF-P1 Controlled Mutation

最初のmutation候補は1つだけ選ぶ。

例:

```text
AF0:
  AR-alpha = 3400 Hz

AF1:
  AR-alpha = 3800 Hz

others:
  identical
```

または:

```text
HL-alpha even = 0.72 → 0.50
```

一度に複数変えない。

## AF-P2 Synthetic Crossover

例:

```text
Parent A
  HL-A
  AR-A

Parent B
  HL-B
  AR-B

Child
  HL-A
  AR-B
```

P0時点では未実装。

---

# 32. AF0を選ぶ理由

AF0の設計は、人工的特徴を3階層へ分散する。

```text
Source:
  Harmonic Lattice

Identity:
  Dual Resonance

Expression:
  Afterglow
```

これにより将来:

```text
Sourceだけ親A
Identityだけ親B
Expressionだけ変異
```

という操作が可能になる。

またAR-αは系譜の最も明確な血統標識になる。

```text
Gen0  3400 Hz
Gen1  3400 Hz
Gen2  3415 Hz
Gen5  3470 Hz
Gen9  lost
```

という履歴が成立すれば、

```text
Founder trait persistence
Founder trait drift
Founder trait loss
```

を定量記録できる。

---

# 33. 完了宣言

全Gate PASS時:

> **VoiceGenesis Artificial Founder Series AF-P0 COMPLETE — Artificial Founder AF0 ESTABLISHED.**

さらに:

> **AF0 Founder Trait Set 0.1 PRESERVED — Harmonic Lattice HL-α、Dual Ancestral Resonance AR-α/β、Afterglow Expression AG-αは、人工始祖の身体生成とVoiceGenesis再表現を通じて事前登録範囲で回収された。**

確認済み:

```text
Source-Free origin
Procedural artificial phonation
UTAU embodiment
VoiceGenesis ingestion
Standard Identity
F0
Duration
Energy
Release
HL-alpha
AR-alpha
AR-beta
AG-alpha
Determinism
Provenance
```

未確認:

```text
Naturalness
Perceptual uniqueness
Learning
Education
Mutation
Crossover
Inheritance
Selection
Multi-generation evolution
Human × Synthetic
```

---

# 34. Primary Repository References

```text
main @ 2a8f69b155407c5fb5f57d4458ba202aa9bd9f1b

voice_genesis/foundry/adapter/donor_bank_utau.py
voice_genesis/foundry/adapter/donor_bank.py
voice_genesis/foundry/adapter/units.py
voice_genesis/foundry/adapter/joins.py
voice_genesis/foundry/adapter/render.py
voice_genesis/foundry/adapter/voice_spec.py

voice_genesis/foundry/s1_dataprep/README.md
voice_genesis/foundry/s1_dataprep/convert_ritsu.py

voice_genesis/foundry/planb_real/results/core_six_scorecard.json
voice_genesis/foundry/genome_s3/results/S3_RECORD.md
voice_genesis/foundry/genome_s35/results/S3_5_RECORD.md
voice_genesis/foundry/genome_s4/results/S4_RECORD.md
voice_genesis/foundry/genome_s4/results/s4b/S4B_RECORD.md

AGENTS.md
LICENSE
```

---

# Appendix A. 実装追補（Implementation Addendum）

**実装日（measured UTC）: 2026-08-21**

本文冒頭の `策定日: 2026-08-22` は設計者の記載をそのまま保存している（原本の
事前登録ヘッダを実装側で書き換えない）。実測 UTC 日付との差は設計者のローカル
暦（JST = UTC+9）由来であり、provenance としては本行の measured UTC 日付が正となる。
以降の追補・実行記録の日付はすべて measured UTC で記す。

本節は **v1.1 本文（事前登録設計）を実装可能にするために必要だった補足**を
逐語記録する。phenotype 数値（§2 の凍結値）・許容値（§18）・Gate（§19）・
Overall Verdict（§20）は一切変更していない。追補はすべて **AF0 の結果を見る前**に
確定したものであり、変更理由も併記する。

## A-1. `generator` へ 2 フィールド追加

```json
"harmonic_phase_scheme": "schroeder",
"breath_noise_db_rel_sustain": -42.0
```

- **`breath_noise_db_rel_sustain`**: §2 の `stochastic breath/noise: minimal and
  deterministic` を数値化したもの。声道フィルタの**手前**で混ぜる（実際の呼気雑音と
  同じ経路）。決定論 PRNG（xorshift64*）で生成し、seed は §9.2 の
  `SHA256(founder_id + "/" + alias + "/" + component)` から取る。
- **`harmonic_phase_scheme`**: 40 倍音を全て位相 0 で足すとクレストファクタが跳ね、
  sustain を -12 dBFS へ正規化した時点で PCM16 がクリップする。Schroeder (1970) の
  低クレスト位相列を使う。位相はスペクトル**振幅**を変えないので、HL-α の奇偶比・
  formant 位置・AR の卓立には影響しない。

## A-2. AR-α は複素モーダル分岐として実装する（§9.3 / §10 の具体化）

§10 は「残光は共鳴器の decay として表現する」と定める。ところが帯域幅 220 Hz の
共鳴器の自然減衰は τ = 1/(π·BW) ≈ 1.45 ms で、35 ms は残らない。そこで AR-α を
**複素 1 極共鳴器**（状態 = モーダル複素振幅）として実装し、

- 本体区間は `y[n] = p·y[n-1] + x[n]`（p = r·e^{jω}）の実部 2Re(y) を分岐出力とし、
- main taper が digital zero に達したあとは、その時点のモーダル状態から位相連続に
  回転を続け、**AR-α 専用の 155 ms 半コサイン包絡**（= 120 ms main + 35 ms 残光）で
  減衰させる。

これにより §10 の「AR-α だけを 35 ms 長く残す／全帯域を延長しない」が厳密に成立する。
副次的に、残光区間は AR-α 単独の純音になるため、**測定器は母音に依らず AR-α 中心を
盲目のまま同定できる**（F0 = 261.6 Hz の倍音格子では /i/ の F3 = 3300 Hz と
AR-α = 3400 Hz を sustain スペクトルから分離できない）。

## A-3. 母音 stage は**並列** formant バンクにする

F1/F2/F3 を縦続（cascade）にすると F3 より上が -12 dB/oct × 3 段で落ち、/u/ や /o/ の
5 kHz 帯が h1 比 -115 dB まで沈む。PCM16 の量子化床（-96 dBFS、8192 点 FFT で
h1 比 ≈ -113 dB）より下なので、**AR-β（5100 Hz）は原理的に測定不能**になり、
§18 の `AR-β center ±90 Hz` を満たしようがない。実音声の声道は F4 以上の極を持ち、
4–6 kHz にエネルギーが残る。§8 `origin.generic_phonetic_topology_used: true` の範囲で、
Holmes 型の並列 formant バンク（分岐極性 +,-,+）を採る。F3 より上の裾が 1 段分に
なり、実音声と同程度の高域ダイナミックレンジが残る。

## A-4. AR-β の gain は仕様から導出する

§8 の AR-β は `gain_db` を持たず `beta_alpha_energy_ratio` しか持たない。したがって
gain は仕様値ではなく**仕様から導かれる量**であり、`af_filter.calibrate_ar_beta_gain`
が 1 箇所だけで導出する。定義（事前登録）:

```text
ratio     = 10 ** ((prom_beta_db - prom_alpha_db) / 10)
prom_X_db = 20log10|1 + g_X · H_X(f_X)|      (各共鳴の中心での卓立)
```

測定側（`af_measure.fit_envelope_model`）は peaking 山の**頂点**を推定するので、
校正側も中心での卓立で定義を揃える。

## A-5. Energy は**出力側**で平坦化する

§2 は `Energy: highly regular` / `sustain_ripple_db = 0` を phenotype として定める。
一方 §10 の terminal F0 fall は倍音格子を下へずらすので、母音共鳴（例 /a/ の
F1 = 720 Hz, BW = 70 Hz）を通した**出力**振幅は F0 の下降だけで約 3 dB 動く。源側
だけを一定にすると仕様の Energy 形質が出力に現れない。`af_expression.regularity_gain`
が出力の短時間 RMS を設計包絡へ合わせる時変スカラーゲインを作る。スカラー倍なので
スペクトル比（HL-α 奇偶比・AR 卓立・formant 位置）は変えない。

なお `attack_ms = 30` は **10–90% 立ち上がり時間**として実現する（線形ランプ長
`attack_ms / 0.8`）。§15.6 の測定定義と同じ量になるようにするため。

## A-6. terminal F0 fall は articulation の末尾で完了する

§10 の並び（terminal F0 fall → main amplitude taper）をそのまま採る。F0 の下降は
articulation の最後の `terminal_window_ms = 80 ms` で完了し、release taper 中は
下降後の値を保持する。これにより terminal delta は振幅がまだ十分ある領域で測定でき、
taper 末端の無信号区間で F0 を推定する必要がない。

## A-7. 測定器側の事前登録（`criteria.metric_definitions`）

`af_measure` は AF0 の設計値へ到達できない（`af_spec` すら import しない）。
測定の「どう測るか」はすべて `criteria/AF_P0_CRITERIA.json` の
`metric_definitions` に事前登録されており、以下を含む。

| 推定器 | 内容 | 採用理由 |
|---|---|---|
| `envelope_model` | 倍音振幅(dB) = 源ロールオフ + parity 段差 + 並列 formant バンク + AR-α/β の peaking 山。線形係数は厳密最小二乗、非線形（中心/帯域）は決定論的座標降下 | §15.2「複数倍音の band-normalized aggregation」・§15.3「既知の局所 band model」の具体化。261 Hz 間隔の倍音格子ではピーク拾いで /i/ の F1 = 280 Hz を取れない |
| 残光プローブ | 終端 20 ms の FFT ピーク（§10 により AR-α 単独） | AR-α 中心を母音に依らず 5/5 で同定するため |
| `fit_half_cosine_decay` | `u = acos(2·env/level - 1)/π` の線形化 + 最小二乗直線 | t90/t10 の 2 点交差は勾配が浅く、包絡リップルで数 ms 滑る |
| `refine_f0_from_harmonics` | `F0 = Σ h·f_h / Σ h²` | 自己相関だけでは ≈3.5 cent 残り、§18.1 の `<= 5 cent` に余裕が無い |
| 残光 disappearance の基準レベル | sustain 区間の中央値（全体ピークではない） | /k/ burst が 3.4 kHz 帯へ漏れて基準を持ち上げ、unit ごとに数 ms ずれるのを防ぐ |

集計規約（事前登録）:

- `beta_alpha_energy_ratio` は **5 母音の中央値**で判定する（`ratio_aggregate`）。
  /i/ は F3 = 3300 Hz が AR-α 帯へ入り込むため per-vowel の卓立分解が悪条件になる。
- `formants` / `harmonic_lattice` / `envelope_model` / `founder_resonance` は 5 母音、
  `duration` は ら行 5 unit、`energy attack` は 5 母音、それ以外（F0 / sustain /
  release / afterglow / terminal zero）は全 25 unit で測る。

## A-8. 計器の修正は AF0 の形質結果を見る**前**に限る

§15.3 の「結果を見て peak finder を変更しない」を守るため、実行順（§23）は
Phase 1（Meter control）を Phase 5（Body measurement）より前に置く。本追補に列挙した
推定器の調整はすべて control fixture と合成信号の上で行い、AF0 canonical run の
trait 結果を見てからの変更は行っていない。
