# RUN9-L0-HARNESS-3c — PRACTICE Alignment W2 実測記録

- 実測日: 2026-08-28 UTC
- 判定: **W2_ESTABLISHED**
- 対象: sanitized score projection generator と Founder-local prototype DP alignment の決定論・成立性
- 対象外: `.lab` による境界精度監査（`r_practice` freeze 後の共通外部監査に限定）

## 1. 事前凍結した境界

PRACTICE actor が受け取る入力は、24-bit / mono / 48 kHz PCM WAV と、次の6要素だけを含む sanitized score projection に限定した。

1. `mora_order`
2. `mora_count`
3. `nominal_duration_ratio`
4. `phrase_grouping`
5. `lyrics_phoneme_sequence`
6. `nominal_pitch`

projection generator は MusicXML だけを読み、actor aligner は MusicXML を受け取らない。PJS MusicXML は lyric を持つが phoneme tag を持たないため、`phoneme_sequence=[]` として「情報なし」を明記し、phoneme を推測しない。

実装は `education_lesson_builder.py` を import しない独立モジュールとした。actor preflight は入力を1つも open する前に、直接 `.lab` path、`.lab` へ解決される symlink、audit manifest、education lesson manifest、full consumed-inputs manifest を拒否する。

## 2. prototype DP の固定内容

- algorithm id: `monotonic_segment_dp_v1`
- frame: 40 ms window / 10 ms hop
- audio feature:
  - RMS dB
  - positive dB delta を p95 で正規化した onset evidence
  - active 範囲内 energy span で正規化した low-energy complement
  - mora の nominal pitch lag における normalized autocorrelation
- active extent: peak RMS dB から -30 dB 以内の最初/最後の frame、両端2 frame padding
- path: `mora_count` 個の segment、挿入・削除なし、先頭/末尾は active extent に固定、境界は狭義単調
- duration constraint: 各 mora 3 frame 以上、score nominal duration に対して `[0.35, 2.80]`
- transition cost:

  `1.25 * ln(observed_frames / target_frames)^2 + (1 - mean_pitch_autocorrelation) + 0.35 * (1 - boundary_strength)`

  最終境界では boundary 項を加えない。
- tie-break: cost 差が `1e-12` 以下なら predecessor frame index が小さい方
- failure:
  - active audio 不成立
  - `mora_count * 3` frame 未満
  - nominal pitch が v1 の autocorrelation lag 範囲外
  - duration constraint を満たす完全 path なし
  - non-finite cost
  - normalized cost `> 3.0`

failure は `ALIGNMENT_FAILED` と空 boundary 列を返す。loss evaluator 側では candidate 全体を `NOT_SCORABLE` とし、zero fill、推測補完、reseed、追加 trial、予算追加を行わない。

## 3. 実測入力

公開 PJS corpus ver 1.1 archive を取得し、使用前に実バイト hash を検証した。

- archive file: `PJS_corpus_ver1.1.zip`
- bytes: `275179158`
- measured SHA-256: `683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca`
- expected SHA-256: `683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca`
- match: `true`

固定 smoke subset は `pjs001`, `pjs002`, `pjs003`。各 song について `*.musicxml` と `*_song.wav` だけを archive から展開した。`.lab` は展開・open・parse していない。

## 4. 独立2プロセス決定論実測

実行コマンド（同一コマンドを fresh Python process で2回）:

```bash
python voice_genesis/evolution/run9_dual_founder_pjs/practice_alignment.py \
  measure-w2 <PJS_SUBSET_ROOT> pjs001 pjs002 pjs003
```

- run 1 stdout SHA-256: `f37f387edefac03ac06738ab3838292e29a11ab198de2d846b41ab6e774d2d73`
- run 2 stdout SHA-256: `f37f387edefac03ac06738ab3838292e29a11ab198de2d846b41ab6e774d2d73`
- stdout byte-identical: `true`
- `overall_pass`: `true`

| song | mora | boundary | status | monotonic | normalized cost | projection SHA-256 | alignment SHA-256 |
|---|---:|---:|---|---|---:|---|---|
| pjs001 | 42 | 43 | ALIGNED | true | 0.5451710083530097 | `07ad03c0b7defb69b7130a58cc7ab12f800ad8a863f1f723a2fe54258eec9421` | `ef358a03266a017699b717f4cfcd98c1f93c30b43d465540258223964430bea6` |
| pjs002 | 46 | 47 | ALIGNED | true | 0.44991423421469817 | `5526d2bf7959e1ecc448fa38235cad71b77f1b1c968a7d3cf871d88362c28c17` | `cca1bd6ebdf09a62d34fe91fe1998e29de8338e2f619bf5b4f3976b81bebd557` |
| pjs003 | 35 | 36 | ALIGNED | true | 0.5565801701127947 | `bc6e44ef31937d53e45080a8128d3ca71a44f8588bee1b3ada5b3dfb1a3835e8` | `cac4784a3f4b6782217e3cc81f23c45e00e2cf75a44090f99098fcfa46d6e094` |

### training 70曲の成立性 sweep

3曲の独立2プロセス測定とは別に、frozen practice split の training 70曲を manifest 順のまま全件 sweep した。ZIP 内から各 `*.musicxml` / `*_song.wav` member を名前指定で読み、`.lab` member は列挙・read・extract していない。

- score projection generation: `70 / 70` 成功
- total mora: `2823`
- ordered `(song_id, mora_count, projection_sha256)` digest: `27cd6d1135ec9d8a43033ebade4e3dfaa4f9391631351538b196ee0893621073`
- DP alignment: `70 / 70` `ALIGNED`
- `ALIGNMENT_FAILED`: `0`
- maximum normalized cost: `0.725643819987552`
- ordered 70 alignment result digest: `fd23d7fe9599dce85627e4cd5fc8a4e716dd221bd58e18063d9da9febdf9a07f`

この sweep は成立範囲の確認であり、独立2プロセス byte equality は前節の固定3曲に対して行った。

## 5. 自動テスト

`tests/test_practice_alignment.py` で以下を検査した。

- score projection の exact 6 fields と同一 bytes
- synthetic tone に対する同一 alignment bytes、完全・狭義単調境界
- 無音入力の `ALIGNMENT_FAILED` と空境界（zero fill なし）
- unknown teacher-boundary field の拒否
- `.lab` 直指定の read 前拒否
- `.lab` symlink の read 前拒否
- audit manifest の WAV open 前拒否
- PRACTICE module import graph から EDUCATION `.lab` module を排除

結果:

```text
8 passed in 1.32s
ruff: All checks passed!
```

測定時 runtime: Python `3.12.13`, NumPy `2.3.5`。実装 SHA-256 は `d7ee70510c42b106f8d8dfb2ab34f9c7c54ffa0f1f5459234a3d13102b3321b8`、test SHA-256 は `7f894e0be115f28aed6ac57fa0bc52c5638a407a0a2438a8b3cf517ca85a91f8`。

## 6. 事実と主張上限

### 事実

- verified PJS 3曲で、projection と DP output は独立2プロセス間 byte-identical だった。
- 3曲すべてで、`mora_count + 1` 個の狭義単調境界を生成できた。
- training 70曲すべてで projection generation と DP alignment が成立した。
- actor path の `.lab` 直接・symlink・audit manifest は read 前に fail-closed した。
- synthetic silence は境界を補完せず `ALIGNMENT_FAILED` になった。

### この記録からは主張しないこと

- `.lab` に対する境界精度は測っていない。裁定により、その比較は `r_practice` freeze 後の common external audit でのみ可能である。
- 3曲 smoke は全 training split の精度保証ではない。
- prototype alignment 成立は、Birth Gate PASS、identity establishment、または learning recipe 実行可能性を意味しない。

したがって本 W2 の判定は「許可入力だけで deterministic な alignment path が成立し、PRACTICE 再凍結へ進める」に限定する。
