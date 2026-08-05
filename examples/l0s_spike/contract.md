# CompositionScore 著述契約 v0（L0-s 最小版）

この文書は、あなた（著者）が CompositionScore を書くために参照できる**唯一の仕様**である。
この文書と、別途渡される課題文・前周の自作ファイル・前周の報告以外の情報は存在しない
前提で著述すること。

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
  使ってはならない。この契約の外の情報を得た場合、その周回は無効になる）。

## 1. score.yaml スキーマ（公開範囲の全部）

トップレベルは次の 5 キーが必須、`events` のみ任意。**ここに列挙されていないキーを
どの階層にも書いてはならない（未知キーは全て検証エラーで拒否される）**。

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
  bpm: int              # テンポ。整数のみ（文字列・小数不可）
  key: str              # 調。形式 "<A-G>[#|b] <major|minor>"。例 "D minor" "F# major"
  time_signature: str   # 拍子。例 "4/4"
  active_rate_target: str   # 発音密度目標。"0.85-0.92" 形式（本課題の判定対象外）
  valley_depth_target: str  # 谷深さ目標。"0.15-0.25" 形式（本課題の判定対象外）
  brightness: str       # 明度。"dark" / "bright" / "balanced" のいずれか
  stereo_width: str     # 例 "narrow" / "wide"（本課題の判定対象外）

structure:       # 必須（セクションのリスト。演奏順に並ぶ）
  - section: str  # セクション名（小文字推奨。例 intro / chorus / outro）
    bars: int     # 小節数（秒指定は存在しない。実時間 = bars × 拍数 × 60/bpm）
    role: str     # 役割の一文（自由文）
    physical: str # 演奏ヒント（自由文だが、下記 §2 の認識語彙のみが演奏に反映される）

rendering:       # 必須（本課題では固定値を推奨）
  target_backend: "external"
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
- **構造の観測はあなたの宣言の転記ではない**: 観測器は完成音源のエネルギー・音色の
  変化からセクション境界を自動検出し、検出されたセクション列に
  {intro, verse, chorus, bridge, outro, full} 語彙のラベルをヒューリスティックに
  付与する。宣言したセクション名がそのまま観測される保証はない。
  隣接セクションの対比（密度・音量の差）が明確なほど、境界は宣言どおりに
  検出されやすい。極端に短い曲（数秒）は 1 セクションに縮退する。
- キーと brightness もあなたの宣言の転記ではなく、完成音源からの実測である。

## 3. 差分報告の読み方（毎周回あなたに返る唯一の観測情報）

記号検証（スキーマ検証）に不合格の場合、`validation` にエラーのリストが返る:

```yaml
round: <N>
symbolic_validation:
  status: fail
  errors:
    - where: <セクション+フィールドの位置>   # 例 "physical.bpm"
      message: <検証器のエラーメッセージ>
```

合格した場合は演奏・計測まで進み、軸別の差分報告が返る:

```yaml
round: <N>
symbolic_validation: {status: pass}
axes:
  key:
    requirement: <課題の要求>
    observed: <実測値>          # band が measured のときのみ信頼できる
    verdict: preserved | deviated
    band: measured | out_of_band | not_observed
  brightness:
    requirement: ...
    observed: ...
    verdict: preserved | deviated
    band: ...
  structure:
    requirement: [intro, chorus, outro]
    observed: [<観測されたラベル列>]
    verdict: exact_match | mismatch
    band: ...
notes: []   # 補足があれば構造化した短文で
```

- `band: measured` 以外の数値・ラベルは修正の根拠に使ってはならない。
- 全軸 verdict が preserved / exact_match になれば課題達成である。
