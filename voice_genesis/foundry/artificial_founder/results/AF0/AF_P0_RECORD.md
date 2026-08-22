# AF-P0 RECORD — Artificial Founder AF0

- **Overall verdict**: `NOT_ESTABLISHED`
- **Reason codes**: ['DURATION_NOT_ESTABLISHED', 'ENERGY_NOT_ESTABLISHED', 'AFTERGLOW_NOT_ESTABLISHED']
- **Phenotype**: Dual Resonance / Afterglow
- **Spec sha256**: `c477fd5a9ec2ac3dd97f2c7ea076568acce19b53c28eed5c10175cc807b5e8d4`

本記録は事前登録設計 `DESIGN_AF_P0.md` に対する実行結果である。
許容値・候補・Gate は結果を見て変更していない。

## Gates

| Gate | Name | Verdict | Reason |
|---|---|---|---|
| G0 | SOURCE_FREE | PASS |  |
| G1 | SPEC_VALID | PASS |  |
| G2 | DETERMINISTIC_COMPILATION | PASS |  |
| G3 | UTAU_BODY | PASS |  |
| G4 | VOICEGENESIS_INGESTION | PASS |  |
| G5 | METER_CONTROL | PASS |  |
| G6 | STANDARD_IDENTITY | PASS |  |
| G7 | FOUNDER_SOURCE_HL | PASS |  |
| G8 | FOUNDER_IDENTITY_AR | PASS |  |
| G9 | F0 | PASS |  |
| G10 | DURATION | FAIL | DURATION_NOT_ESTABLISHED |
| G11 | ENERGY | FAIL | ENERGY_NOT_ESTABLISHED |
| G12 | RELEASE | PASS |  |
| G13 | FOUNDER_EXPRESSION_AG | FAIL | AFTERGLOW_NOT_ESTABLISHED |
| G14 | PROVENANCE_AND_PUBLICATION | PASS |  |

## Body traits (§18.1)

| Trait | Verdict | Worst error / detail |
|---|---|---|
| afterglow | PASS | worst=3.635 / tol=5.0 |
| ar_alpha | PASS | worst=0.05203 / tol=50.0 |
| ar_beta | PASS | worst=27.39 / tol=90.0 |
| duration_onset | PASS | worst=0.801 / tol=2.0 |
| duration_share | PASS | worst=0.00571 / tol=0.02 |
| energy_attack | PASS | worst=2.576 / tol=5.0 |
| energy_sustain | PASS | worst=0.02487 / tol=0.5 |
| f0_core | PASS | worst=0.02071 / tol=5.0 |
| hl_alpha | PASS | worst=0.002853 / tol=0.08 |
| identity | PASS | 15/15 peaks (need 13) |
| release | PASS | worst=1.804 / tol=2.0 |
| terminal_f0 | PASS | worst=2.718 / tol=15.0 |
| terminal_zero | PASS |  |

## VoiceGenesis re-expression traits (§18.2)

| Trait | Verdict | Worst error / detail |
|---|---|---|
| afterglow | FAIL | worst=59.36 / tol=20.0 / failed=['u', 'ku', 'ko', 'su', 'so', 'ru'] |
| ar_alpha | PASS | worst=41.63 / tol=150.0 |
| ar_beta | PASS | worst=29.55 / tol=220.0 |
| duration_onset | FAIL | worst=21.98 / tol=15.0 / failed=['ra', 'ri', 'ru', 're', 'ro'] |
| duration_share | PASS | worst=0.07739 / tol=0.08 |
| energy_sustain | FAIL | worst=3.002 / tol=2.0 / failed=['i', 'ki', 'shi', 'ni', 'ri'] |
| f0_core | PASS | worst=0.5239 / tol=25.0 |
| hl_alpha | PASS | worst=0.05306 / tol=0.15 |
| release | PASS | worst=19.71 / tol=30.0 |
| spectral_identity | PASS | median=0.9846 / each>=0.85 |
| terminal_f0 | PASS | worst=3.922 / tol=50.0 |

## 失敗の切り分け（§16 二段測定）

Body（44.1 kHz voicebank）と VoiceGenesis re-expression（WORLD 24 kHz）の
どちらで形質が落ちたかを分ける。compiler 失敗 / body 表現失敗 / WORLD 分析・
再合成での形質喪失を混同しないための §16 の目的そのもの。

| Gate | Name | 落ちた段 |
|---|---|---|
| G10 | DURATION | re-expression |
| G11 | ENERGY | re-expression |
| G13 | FOUNDER_EXPRESSION_AG | re-expression |

## Claim ceiling (§4)

PASS 時に言ってよいこと:

- Artificial Founder AF0 ESTABLISHED
- Founder Trait Set AF0/0.1 PRESERVED

PASS でも言ってはいけないこと:

- 人間のように自然
- 完成した日本語歌手
- 人工生命が成立
- gene が教育可能
- 世代遺伝が成立
- crossover 成立
- Founder Trait が知覚的に顕著
- HL-α / AR-α / AG-α が進化的に有利
- AF0 に人格や意識がある

## 未確認 (§33)

- Naturalness
- Perceptual uniqueness
- Learning
- Education
- Mutation
- Crossover
- Inheritance
- Selection
- Multi-generation evolution
- Human x Synthetic

## Environment / pins

```json
{"body_identity_digest":"5a5702cb453768265c390fc2eeabd3a07dad6194c0a6a426eedc0df239a7d6ec","code_closure_sha256":"684807c169dbb18fd190547ec31605857c17b4df5ff895eacdaf60a6c603dd02","controls_sha256":"b4bcd0278099fb485741b0529c4f166a7f578321d88d20f1b62f4ba279da4079","criteria_sha256":"3d6ea665fb426ad0edf19433327de4b9c9df3a576f7ebe92f3881bcab827858a","numpy":"2.4.6","probes_sha256":"6061b71adc57af6e8b7cc39b27735e2818f12b4fa2822613c665f0e2b927d60c","python":"3.11.15","pyworld":"0.3.5","scipy":"1.17.1","spec_sha256":"c477fd5a9ec2ac3dd97f2c7ea076568acce19b53c28eed5c10175cc807b5e8d4"}
```
