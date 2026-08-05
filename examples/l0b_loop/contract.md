# CompositionScore 著述契約 v1（L0b 版）

この文書は、あなた（著者）が CompositionScore を書くために参照できる**唯一の
仕様**である。この文書と、別途渡される課題文（`task.md`）・前周の自作
ファイル・前周の差分報告以外の情報は存在しない前提で著述すること
（正本 D2: アダプター = 情報の国境）。

本契約は L0-s 契約 v0（`examples/l0s_spike/contract.md`）の改訂版である。
公開スキーマ範囲の正本は宣言的 YAML spec
（[`config/authoring_contract_l0.yaml`](../../config/authoring_contract_l0.yaml)、
L0a で凍結済み）に一本化されており、以下の §1 はその spec の逐語的な人間可読
版である——spec と本文書が食い違った場合は spec が正（`svprpe validate` が
実際に強制するのは spec の内容そのもの）。

## 0. あなたの役割と出力

- あなたは作曲者である。課題文の要求を満たす楽曲を **CompositionScore**（YAML）で著述する。
- 楽曲はエンジン側の決定論演奏者が音にし、計器が観測して要求との差分を報告する。
  あなたは報告だけを見て Score を修正する。
- **出力は次の 2 つの YAML 文書のみ**。他の文章・前置き・解説は書かない。
  1. `score.yaml` — CompositionScore 本体（下記スキーマに厳密準拠）
  2. `intent.yaml` — 意図メモ（自由形式 YAML。「なぜこの設計か」をセクション毎に短く。
     エンジンは読まない。人間の参照専用）
- 出力形式: ` ```yaml score` フェンスと ` ```yaml intent` フェンスの 2 ブロック。
- 最終行に `tools_used: none` と宣言すること（あなたはツール・ファイル参照を一切
  使ってはならない。この契約の外の情報を得た場合、その周回は off-contract
  イベントとして記録され、契約遵守の収束実績に数えない）。

## 1. score.yaml スキーマ（公開範囲の全部）

トップレベルは次の 5 キーが必須、`events` のみ任意。**ここに列挙されていない
キーをどの階層にも書いてはならない（未知キーは全て検証エラーで拒否される）**。

```yaml
meta:            # 必須
  title: str     # 曲名
  version: str   # 例 "0.1"

semantic:        # 必須（意味層。演奏には限定的にしか反映されない）
  core: str      # 曲の雰囲気の一文
  grv:
    primary: str    # 主ジャンル/グルーヴ語（自由語彙）
    secondary: str  # 副
  delta_e:
    overall: str    # 曲全体の情動変化の一文
  avoid: [str]      # 避けたい要素のリスト（空リスト可）

physical:        # 必須（物理層。7 フィールド全て必須）
  bpm: int              # テンポ。整数のみ、かつ >= 1（0 以下は決定論演奏者を
                         # クラッシュさせる — 実測確認済み。文字列・小数不可）
  key: str              # 調。形式 "^[A-Ga-g][#b]? (major|minor)$"。
                         # 例 "D minor" "F# major"（この形式に一致しないと
                         # 決定論演奏者をクラッシュさせる — 実測確認済み）
  time_signature: str   # 拍子。形式 "^[1-9][0-9]*/[1-9][0-9]*$"
                         # （分子・分母とも 1 以上の整数。例 "4/4"。分子 0 は
                         # 決定論演奏者をクラッシュさせる — 実測確認済み）
  active_rate_target: str   # 発音密度目標。"0.85-0.92" 形式（本課題の判定対象外）
  valley_depth_target: str  # 谷深さ目標。"0.15-0.25" 形式（本課題の判定対象外）
  brightness: str       # 明度。"dark" / "bright" / "balanced" のいずれか
  stereo_width: str     # 例 "narrow" / "wide"（本課題の判定対象外）

structure:       # 必須（セクションのリスト。演奏順に並ぶ。
                  # **最低 1 要素必須** — 空リストは決定論演奏者をクラッシュ
                  # させる。実測確認済み）
  - section: str  # セクション名（小文字推奨。例 intro / chorus / outro）
    bars: int     # 小節数、かつ >= 1（0 以下は決定論演奏者をクラッシュさせる
                  # — 実測確認済み。秒指定は存在しない。
                  # 実時間 = bars × 拍数 × 60/bpm）
    role: str     # 役割の一文（自由文）
    physical: str # 演奏ヒント（自由文だが、下記 §2 の認識語彙のみが演奏に反映される）

rendering:       # 必須（本課題では固定値を推奨）
  target_backend: "external"   # リテラル固定値。他の文字列（例 "musicgen"）は
                                # 決定論演奏者のパッケージ生成をクラッシュさせる
                                # — 実測確認済み。"suno" はクラッシュしないが
                                # 判定器の意味論を無言で変えるため同様に拒否される
  prompt_max_chars: 650
  priority:             # 描画優先順のトークン列（そのまま流用してよい）
    - semantic.core
    - physical.key
    - physical.bpm
    - structure

events:          # 任意（書く場合のみ）
  chord_progression:    # コード進行。省略時はキーに応じた既定進行が演奏される
    - root: str         # "C" "C#" "D" "D#" "E" "F" "F#" "G" "G#" "A" "A#" "B" のみ
                        # （フラット表記不可。Bb は "A#" と書く）
      quality: str      # "major" / "minor" のみ
```

`fixity:` と `control_profile:` はこの契約の公開範囲外である。書いてはならない。

**構造的な注意**: 同じトップレベルキー（例 `physical:`）を YAML 文書内で 2 回
書かないこと——重複キーは検証器がエラーとして拒否する（後勝ちの黙った上書きを
許さない）。

## 2. 演奏の仕組み（著述ガイド）

- 演奏者は**決定論シンセ**である。同じ Score からは常に同じ音が出る。
- `physical.key` と `events.chord_progression`（省略時は調の既定進行）が和声を決める。
- `physical.brightness` は音色に直結する: `"dark"` は倍音の少ない低域中心の音色、
  `"bright"` は倍音の多い高域寄りの音色になる。
- `structure` の各セクションは宣言順に `bars` の長さだけ演奏される。
- `structure[].physical` の自由文のうち、演奏者が認識する手掛かり語は次のみ:
  `"silence"`, `"no kick"`, `"low density"`, `"sub bass"`, `"sparse"`,
  `"full energy"`, `"release"`, `"rest"`。
  これらはセクションの音量・密度・持続音の性格を変える。認識語を含まない自由文は
  既定の中庸な演奏になる。

### 構造センサーの分解能・可行域（v0 からの新規開示）

- **構造の観測はあなたの宣言の転記ではない**: 観測器は完成音源のエネルギー・
  音色の変化からセクション境界を自動検出し、検出されたセクション列に
  {intro, verse, chorus, bridge, outro, full} 語彙のラベルをヒューリスティックに
  付与する。宣言したセクション名がそのまま観測される保証はない。
- **境界検出は音響駆動**: 隣接セクションのエネルギー・音色対比が明確なほど、
  境界は宣言どおりに検出されやすい。
- **最小セクション間隔は概ね 5 秒**。極端に短い曲は 1 セクションへ縮退する。
- **長い持続区間は内部ダイナミクスの変化で過分割されうる**——1 つの
  セクションのつもりで書いた長い区間が、内部の音量・密度変化により観測側で
  2 つ以上に分割されて観測されることがある。
- **可行窓は狭い**（実測点。正確な閾値ではない——新しい課題を組む際は自前の
  陽性対照で可行性を確認すること）:
  - 中間セクション実長 **12.3 秒は分割された**（長すぎて過分割された側）。
  - 中間セクション実長 **6.2 秒は吸収された**（短すぎて縮退した側）。
  - 陽性対照の中間セクション実長 **7.5 秒は 3 分割ちょうどで成立した**。
  - この 2 点（12.3s 分割・6.2s 吸収）と 1 点（7.5s 成立）から、可行窓が
    おおよそ 7.5 秒近辺の狭い帯にあることが示唆される。中間セクションの
    実時間（`bars × 拍数 × 60/bpm`）をこの帯に近づけて設計すること。
- キーと brightness もあなたの宣言の転記ではなく、完成音源からの実測である。

## 3. 差分報告の読み方（毎周回あなたに返る唯一の観測情報）

差分報告は `AuthoringDiffReport` 正規形（JSON）で返る（L0-s の自由形式 YAML
報告から更新——スキーマが固定され、境界秒が新たに載る）。

記号検証（`svprpe validate --contract`）に不合格の場合:

```json
{
  "schema_version": "authoring-diff-report/1.0",
  "round": <N>,
  "symbolic_validation": {
    "status": "fail",
    "errors": [
      {"where": "physical.bpm", "message": "<検証器のエラーメッセージ>", "kind": "range"}
    ]
  },
  "axes": {},
  "notes": []
}
```

- `errors[].kind` の語彙: `public_scope`（契約非掲載キー） /
  `type`（型不一致） / `enum`（列挙外） / `literal`（リテラル値不一致） /
  `format`（形式不一致、`physical.key`/`physical.time_signature` のみ） /
  `range`（値域下限またはリスト最小要素数を下回る） / `canonical`
  （CompositionScore 検証エラー）。

記号検証に合格した場合は演奏・計測まで進み、軸別の差分報告が返る:

```json
{
  "schema_version": "authoring-diff-report/1.0",
  "round": <N>,
  "symbolic_validation": {"status": "pass"},
  "axes": {
    "key": {
      "requirement": "D minor",
      "observed": "<実測値>",
      "verdict": "preserved" | "deviated",
      "band": "measured" | "out_of_band" | "not_observed"
    },
    "brightness": {
      "requirement": "dark",
      "observed": "<実測値>",
      "verdict": "preserved" | "deviated",
      "band": "measured" | "out_of_band" | "not_observed"
    },
    "structure": {
      "requirement": ["intro", "chorus", "outro"],
      "observed": ["<観測されたラベル列>"],
      "verdict": "exact_match" | "mismatch",
      "band": "measured" | "out_of_band" | "not_observed",
      "observed_sections": [
        {"label": "intro", "start_seconds": 0.0, "end_seconds": 2.5},
        {"label": "chorus", "start_seconds": 2.5, "end_seconds": 6.25},
        {"label": "outro", "start_seconds": 6.25, "end_seconds": 8.75}
      ]
    }
  },
  "notes": [
    {"kind": "position_match_rate", "value": 0.6667}
  ]
}
```

- `band: "measured"` 以外の数値・ラベルは修正の根拠に使ってはならない。
- `axes.structure.observed_sections` は**新規**（v0 契約には無かった）:
  観測された各セクションの `{label, start_seconds, end_seconds}` を秒単位で
  与える。実際に演奏された各セクションの長さがここから逆算できる——
  §2 の可行窓開示と突き合わせて、次周回でセクション長を調整する材料に使う
  こと（例: あるセクションの `end_seconds - start_seconds` が可行窓から
  大きく外れていれば、そのセクションが過分割/吸収された疑いがある）。
- `notes` は構造化された参考値のみを運ぶ（自由文の注釈は禁止）。現在唯一
  許可されている `kind` は `"position_match_rate"`（構造ラベル列の正規化後
  位置一致率、`0.0`–`1.0`）。
- 全軸 verdict が `preserved` / `exact_match` になれば課題達成である。

### Pareto 改善の定義（参考——あなたの修正方針の目安）

各周回の改善は軸別に事前登録された距離で判定される（あなたが直接使う数値
ではないが、「何が改善とみなされるか」を知ることは修正方針の助けになる）:

- `key`/`brightness`: verdict が `preserved` なら距離 0、そうでなければ 1。
- `structure`: 要求列と観測列のラベル単位 Levenshtein 編集距離（大小文字を
  無視した比較）。
- 1 周回が「改善」と判定されるのは、3 軸とも前周から悪化せず、かつ
  少なくとも 1 軸が厳密に改善したときのみ（軸をまたぐ合計スコア化はしない
  ——ある軸の改善で別の軸の悪化を相殺することはできない）。
