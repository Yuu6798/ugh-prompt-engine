# L0b 事前登録課題 — L0B-T1

**Status**: 事前登録（2026-08-05）。陽性対照ゲート通過後に凍結。
正本 = [`docs/llm_adapter_planning.md`](../../docs/llm_adapter_planning.md) §4「L0b」。
本課題の content hash はスパイク開始台帳（`ledger.yaml`）に pin される。
凍結後の変更は禁止（変更が必要になったら別実験として記録し直す）。

## 課題文（著者役へ渡す全文）

課題は L0S-T1（`examples/l0s_spike/task.md`）と**同一の要求**である —
契約 v1（可行域開示）の効果を「同じ課題への挑戦」という条件を揃えた上で
直接測定するため、課題文自体は変えない:

> 3 セクションの短い器楽曲を CompositionScore として著述せよ。要求は次の 3 点:
>
> - **R1（構造）**: 構造観測器が観測するセクションラベル列が、正確に
>   `intro → chorus → outro` の 3 要素列であること。
> - **R2（キー）**: 抽出されるキーが D minor であること（異名同音は等価）。
> - **R3（明度）**: トラック全体の brightness が dark 帯と観測されること。
>
> 旋律の指定は行わないこと（旋律軸は本実験の対象外）。

## 判定器（凍結。L0a 凍結部品へ更新）

L0S-T1 の判定器は L0-s 専用スクリプト（`validate_score.py`/`measure_round.py`、
いずれも凍結済み歴史的成果物）で実装されていたが、L0b は L0a が凍結した
汎用部品を判定器として使う:

| 要求 | 出典計器 | 判定器 | 粒度 |
|---|---|---|---|
| 記号ゲート | — | `svprpe validate <score> --contract config/authoring_contract_l0.yaml`（L0a CLI、`src/svp_rpe/authoring/validate.py`） | Score 全体 |
| R1（構造） | AR4 `svprpe observe` structure domain | canonical `["intro","chorus","outro"]`（section-map/0.1・`frozen/section_map.json`、L0S-T1 と同一）との列完全一致（`sequence_exact_match`） | ラベル列単位 |
| R2（キー） | R0 roundtrip（`svprpe roundtrip` 内蔵の決定論演奏→抽出→比較） | key diagnosis の観測値が `svp_rpe.keys.keys_enharmonically_equal(REQUIREMENT_KEY, observed)` を満たすか（band=="measured" のときのみ） | トラック単位 |
| R3（明度） | 同上（brightness センサー = spectral_centroid、dark ≤ 1200 Hz） | brightness の観測値が `"dark"` と文字列一致するか（band=="measured" のときのみ） | トラック単位 |

- 記号検証ゲートは L0a CLI（`svprpe validate --contract`）を使う（L0-s の
  `load_composition_score` 直呼びから更新——公開スキーマ範囲チェックが
  L0a で追加されている）。
- 報告は L0a の `AuthoringDiffReport` 正規形（`src/svp_rpe/authoring/report.py`）。
  L0S-T1 の自由形式報告からの更新点は「(1) 境界秒の live 配線
  （`axes.structure.observed_sections`）、(2) 帯域・verdict の語彙が
  L0a のスキーマで固定される、(3) notes が `position_match_rate` の
  構造化 kind のみに制限される」の 3 点（詳細 = `contract.md` §3）。
- score-adherence の判定軸は提出 Score の `control_profile` に依存させず、
  凍結軸表 `frozen/eval_control_profile.yaml`（suno: key=tight,
  brightness=tight。L0S-T1 の凍結軸表と同一内容）を注入した評価用コピーで
  実行する（正本 §3 の D7 違反経路遮断。L0S-T1 と同一の運用）。

## L0-s との比較設計（契約 v1 効果の直接測定）

L0B-T1 は L0S-T1 と**課題文・判定軸・凍結軸表を完全に同一**に保つ——変える
のは著者に渡す契約（v0 → v1）と判定器の実装（L0-s 専用スクリプト → L0a
凍結部品）のみである。これにより、L0-s（5 周回、全周回で `structure:
mismatch`——`docs/l0s_spike_record.md` 参照）と L0b の周回間差分を、
「契約 v1 が可行域を開示したことの効果」に直接帰属できる（他の変数を
揃えることで交絡を避ける、正本 §1.2 の実験設計原則）。

具体的に v0 → v1 で著者に新しく開示される情報（`contract.md` §2、出典 =
`docs/l0a_authoring_contract.md` (b)）:

- 構造センサーは音響駆動（宣言の転記ではない）——これは v0 にも記載済み。
- **新規開示**: 最小セクション間隔は概ね 5 秒。長い持続区間は内部
  ダイナミクスの変化で過分割されうる。
- **新規開示**: 可行窓の実測値（L0-s 周回台帳からの実測点）——中間セクション
  実長 12.3 秒は分割された、6.2 秒は吸収された（縮退側）、陽性対照の 7.5 秒は
  3 分割ちょうどで成立した。

L0-s は 5 周回中一度もこの可行窓を著者（実施エージェント）に開示されず、
毎回異なる中間セクション長を試行錯誤で探っていた（`round`5 = intro/outro の
2 セクションへ縮退——過剰修正）。L0b の契約 v1 はこの試行錯誤を可行域の
直接開示で代替できるかを検証する。

## 3 チェック（正本 §4.1 登録前必須。判定器を L0a 凍結部品へ更新）

### チェック 1: 信頼軸（正本 §5）

課題が要求する軸は key・brightness(dark)・構造(セクション) の 3 軸のみ。

- 凍結信頼軸表 = `config/authoring_trusted_axes_l0.yaml`
  （`src/svp_rpe/authoring/trusted_axes.py:derive_trusted_axes()` の凍結物、
  L0a で機械導出済み）。`key`/`brightness`/`structure` の 3 軸はいずれも
  同表に収載済み（`bpm` は `runtime_gate` 付きで収載されているが本課題は
  bpm を要求軸に含めない——不使用）。`brightness` は表の
  `band_restriction.trusted_values: [dark]` どおり dark のみを要求 → 帯内。
- **不使用の確認**: `active_rate_target`/`valley_depth_target`/
  `stereo_width`/`time_signature`（凍結信頼軸表に不収載 = L0a
  `_KNOWN_EXCLUDED_AXES`）、BPM（`runtime_gate` 対象で本課題は事前登録
  していない）はいずれも課題要求に含めない。Score スキーマ上は必須
  フィールドとして記入されるが、判定軸には算入しない。

→ **通過**。

### チェック 2: 観測語彙

L0S-T1 チェック 2 と同一（構造観測器の実装は L0a で変更していない）:
構造ラベルの観測語彙は `assign_labels`（`src/svp_rpe/rpe/structure_labels.py`）の
出力語彙 {Intro, Verse(連番), Chorus, Bridge, Outro, Full} に限られる。
要求列 `intro → chorus → outro` は語彙内、かつ 3 セクション検出時に出力可能な
唯一の列（最初=Intro 固定・最後=Outro 固定・中間 1 件は必ず Chorus に割当たる
実装）であることを実装から確認済み（L0S-T1 で確認済みの実装は不変）。

→ **通過**。

### チェック 3: 観測粒度

L0S-T1 チェック 3 と同一:

- R1 = ラベル列単位（AR4 構造観測はラベル列と列一致のみ）→ 粒度一致。
- R2 / R3 = トラック単位（key・brightness ともトラック単位センサー）→ 粒度一致。
- セクション局所の要求（セクション長比・セクション局所 brightness 対比等）は
  含めない（当該粒度のセンサーが未登録のため）。

→ **通過**。

## Pareto 改善述語

軸別の距離・順序・改善規則は `frozen/pareto.yaml`（schema_version
`l0b-pareto/1.0`）に事前登録・凍結する。判定実装 = `scripts/pareto_eval.py`。
要旨（詳細 = `frozen/pareto.yaml` 本体が正）:

- key/brightness: verdict からの二値距離（`preserved` = 0、それ以外 = 1）。
- structure: canonical 列とのラベル列 Levenshtein 編集距離（casefold 正規化
  後の要素比較）。
- 改善 = 3 軸とも非退行 かつ 少なくとも 1 軸が厳密減少。軸間の集約損失
  （合計スコア化等）は禁止。
- いずれかの軸で `band != "measured"` の周回ペアは改善実績に数えない
  （保守側・D5）。

## 陽性対照ゲート

人手で書いた既知正解 Score（`examples/l0s_spike/positive_control/score.yaml`
— **変更禁止・読み取りのみ**。L0S-T1 の陽性対照をそのまま再利用する。
key/brightness/structure の 3 軸とも観測経路で `preserved`/`exact_match` に
到達済み——L0-s 台帳 `positive_control` 節の実測記録参照）を L0b 経路
（記号検証 → 決定論演奏 → 抽出 → observe 実入力導出 → observe →
`AuthoringDiffReport` 折り込み）に dry-run し、「本課題は L0a 判定器でも
合格可能」かつ「structure 軸の `observed_sections` に境界秒が実際に載る」
ことを開始前に確認する。結果は `examples/l0b_loop/positive_control/report.json`
として保存・pin し、各 content hash は `ledger.yaml` の `positive_control`
節に dated 記録する（pin 既定則。スパイクの evidence には数えない）。
