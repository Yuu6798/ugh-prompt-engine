# L0-s 事前登録課題 — L0S-T1

**Status**: 事前登録（2026-08-05、Fable 設計）。陽性対照ゲート通過後に凍結。
正本 = [`docs/llm_adapter_planning.md`](../../docs/llm_adapter_planning.md) §4.1。
本課題の content hash はスパイク開始台帳（`ledger.yaml`）に pin される。
凍結後の変更は禁止（変更が必要になったら別実験として記録し直す）。

## 課題文（著者役へ渡す全文）

> 3 セクションの短い器楽曲を CompositionScore として著述せよ。要求は次の 3 点:
>
> - **R1（構造）**: 構造観測器が観測するセクションラベル列が、正確に
>   `intro → chorus → outro` の 3 要素列であること。
> - **R2（キー）**: 抽出されるキーが D minor であること（異名同音は等価）。
> - **R3（明度）**: トラック全体の brightness が dark 帯と観測されること。
>
> 旋律の指定は行わないこと（旋律軸は本実験の対象外）。

## 判定器（凍結）

| 要求 | 出典計器 | 判定 | 粒度 |
|---|---|---|---|
| R1 | AR4 `svprpe observe` structure domain | canonical `["intro","chorus","outro"]`（section-map/0.1・`frozen/section_map.json`）との列完全一致（`sequence_exact_match`） | ラベル列単位 |
| R2 | R0 roundtrip（`svprpe roundtrip` 内蔵の決定論演奏→抽出→比較） | key diagnosis == `preserved`（異名同音等価の二値判定） | トラック単位 |
| R3 | 同上（brightness センサー = spectral_centroid、dark ≤ 1200 Hz） | brightness diagnosis == `preserved`（authored `dark` と観測ラベル一致） | トラック単位 |

- 記号検証ゲート = `load_composition_score`（pydantic 検証。音を出さない）。
- score-adherence の判定軸は提出 Score の `control_profile` に依存させず、
  凍結軸表 `frozen/eval_control_profile.yaml`（suno: key=tight, brightness=tight）を
  注入した評価用コピーで実行する（正本 §3 の D7 違反経路遮断）。

## 3 チェック（正本 §4.1 登録前必須）

### チェック 1: 信頼軸（§5）

課題が要求する軸は key・brightness(dark)・構造(セクション) の 3 軸のみ。

- **key / brightness**: R0 診断（`ROUNDTRIP_FIELDS`）出典。両軸とも
  `docs/roundtrip_preservation.md` のスナップショットで `preserved` 到達実績あり
  （knob_dead ではない）。brightness は **dark 帯のみ**が信頼帯（bright 帯は演奏者の
  押し込み不足が実測済み = §5）であり、本課題は dark を要求 → 帯内。
- **構造**: R0 診断対象外のため AR4 観測計器（`svprpe observe` structure domain、
  実配線済み）を出典とする（§5 の規定どおり）。
- **不使用の確認**: `active_rate_target` / `valley_depth_target`（knob_dead）、
  `stereo_width` / `time_signature`（sensor_blind）、BPM（octave/halving 誤検出帯）は
  いずれも課題要求に含めない。Score スキーマ上は必須フィールドとして記入されるが、
  判定軸に算入しない（凍結軸表に含まれない）。

→ **通過**。

### チェック 2: 観測語彙

構造ラベルの観測語彙は `assign_labels`（`src/svp_rpe/rpe/structure_labels.py`）の
出力語彙 {Intro, Verse(連番), Chorus, Bridge, Outro, Full} に限られる。
要求列 `intro → chorus → outro` は語彙内、かつ **3 セクション検出時に
出力可能な唯一の列**（最初=Intro 固定・最後=Outro 固定・中間 1 件は必ず Chorus に
割当たる実装）であることを実装から確認済み。旧課題候補 Intro–Verse–Outro が
3 セクションでは原理的に出力不能である点（PR #244 レビューの境界宣言指摘）の
引き継ぎとして、本課題は実現可能列を要求する。

→ **通過**。

### チェック 3: 観測粒度

- R1 = ラベル列単位（AR4 構造観測はラベル列と列一致のみ）→ 粒度一致。
- R2 / R3 = トラック単位（key・brightness ともトラック単位センサー）→ 粒度一致。
- セクション局所の要求（セクション長比・セクション局所 brightness 対比等）は
  含めない（当該粒度のセンサーが未登録のため。正本 §4.1）。

→ **通過**。

## 陽性対照ゲート

人手で書いた既知正解 Score（`positive_control/score.yaml`）を観測経路
（記号検証 → 決定論演奏 → 抽出 → score-adherence / observe 実入力導出 → observe）に
dry-run し、「本課題は計器で合格可能」を開始前に確認する。陽性対照の Score・
validation・観測・報告の各 content hash は `ledger.yaml` に dated 記録する
（pin 既定則。スパイクの evidence には数えない）。

結果は `ledger.yaml` の `positive_control` 節が正。
