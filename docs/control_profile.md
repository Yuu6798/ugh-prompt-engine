# control_profile — 楽譜が「効くチャネル」を知る（PR1）

`CompositionScore.control_profile` は、**どの生成器でどの物理フィールドが効くか**
（`grip_class`）を楽譜自身に持たせるための optional ブロック。これにより楽譜は
**自己記述的**になり、「AI が演奏できる楽譜」の核心的差別化点となる。

本ドキュメントは PR1（スキーマ + 初期データ）の範囲を記す。実コンパイル
（`control_profile` を `ExternalPromptAdapter` の優先度に配線する）は PR1.5 の範囲。
ロードマップ全体は [`ai_performer_score_roadmap.md`](ai_performer_score_roadmap.md) を参照。

## スキーマ

```yaml
control_profile:                      # key = 生成器名（"suno" / "musicgen" / ...）
  suno:                               # GeneratorProfile = dict[field_name -> ControlGrip]
    bpm:                              # field_name は PhysicalLayer のキー
      grip_class: tight              # tight=保証 / loose=助言 / dead=無効（必須）
      grip: 1.61                     # 効果量 d（K 系列 measure_grip 出力・optional）
      sensor: bpm                    # 観測センサー名（optional）
      evidence: examples/control/k2/expected_grip.json   # 出所参照（optional）
    brightness:
      grip_class: tight
      grip: 0.86
      sensor: spectral_centroid
      evidence: examples/control/k2/expected_grip.json
```

- **`grip_class`**（必須）: `tight`（保証チャネル）/ `loose`（助言・弱い grip）/
  `dead`（無効＝センサー盲 or ツマミ死）。
- **`grip` / `sensor` / `evidence`**（optional）: 値が無ければ serialize から除外される
  （リポジトリの「空なら非 serialize」方針を踏襲）。

## 検証ルール（fixity との差異）

`control_profile` は `fixity` ブロックのパターンを踏襲するが、**網羅必須は引き継がない**。

| ルール | fixity | control_profile |
|---|---|---|
| 未知 field キーを fail-fast | ○ | ○ |
| 全 physical フィールド網羅必須 | ○ | **×（疎を許容）** |
| 不正な値（grip_class）を fail-fast | ○（Literal）| ○（Literal）|

疎を許容するのは、K2（#117）由来の Suno プロファイルが `bpm` / `brightness` の
**2 フィールドのみ**であり、網羅必須だと初期データ自体が弾かれるため。未プロファイルの
フィールドは「未計測＝助言扱い」として正当で、PR1.5 で `rendering.priority` フォールバックに回る。

## フィールド → backend 条件付けチャネル対応表

楽譜の各フィールドが、各生成器でどの条件付けチャネルに乗るかの対応表。
実コンパイラ（チャネルへの実際の流し込み）は PR1.5（テキストプロンプト）以降の範囲で、
本表は設計の見取り図。

| score field | Suno（テキスト生成器）| MusicGen（条件付け生成器）| 備考 |
|---|---|---|---|
| `bpm` | Style 欄 "NNN BPM" | text + melody tempo | K2 で tight 実証 |
| `key` | Style 欄 "Key minor/major" | text | grip 未実証（loose 扱い）|
| `time_signature` | Style 欄 | text | grip 未実証 |
| `brightness` | Style 欄音色語（dark/bright）| text + 音色条件 | K2 で tight 実証 |
| `stereo_width` | Style 欄（弱）| 後処理 | センサー/grip 未実証 |
| `active_rate_target` | 構造記述（弱）| 密度条件（M5）| grip 未実証 |
| `valley_depth_target` | 構造記述（弱）| 制御曲線（M5）| grip 未実証 |

> **正直な限界**: 2026-06 時点で Suno の tight は `bpm` / `brightness` の 2 本のみ。
> 残りは grip 未実証で、PR1.5 では助言（fallback）に格下げされる。芯は K 系列 grip の
> 拡張で後から厚くなり、その都度コード変更なしに助言→保証へ昇格する。

## 初期データの出所

`examples/composition/midnight_signal/composition_score.yaml` の
`control_profile.suno` は K2（#117）の `examples/control/k2/expected_grip.json` から:

| knob | sensor | grip d | class |
|---|---|---:|---|
| bpm | bpm | 1.61 | tight |
| brightness | spectral_centroid | 0.86 | tight |

詳細・留保（BPM prior アトラクタ／brightness の非対称）は
[`controllability_poc.md`](controllability_poc.md) §5.2 を参照。
