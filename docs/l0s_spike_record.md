# L0-s スパイク記録 — LLM 著述トラック第 1 実測（2026-08-05）

**Status**: 完了（2026-08-05 実施）。正本 = [`llm_adapter_planning.md`](llm_adapter_planning.md)
§4.1。本文書はスパイクの観測記録（主生産物 = 契約の欠陥リスト）であり、
L0a 契約凍結の材料。台帳・pin の正 = `examples/l0s_spike/ledger.yaml`。

## 0. 結論サマリー

- **課題 L0S-T1**（構造 intro→chorus→outro / D minor / dark 帯）は 3 チェック +
  陽性対照ゲートを通過して凍結、5 周回を pin 付きで完走した。
- **記号検証ゲートは 5/5 通過** → §5 の停止条件（5 周で記号検証を一度も通過できない
  場合の D3 差し戻し）は**非発動**。
- **物理 2 軸（key / brightness）は 5/5 preserved**。著述契約 v0 の物理層記述は
  この 2 軸には十分だった。
- **構造軸は 5 周とも mismatch**（分割数 8→6→6→4→2。過分割から縮退へ
  オーバーシュートし、正解の 3 に着地しなかった）。陽性対照は合格済みのため、
  未収束は「計器で不可能」ではなく**契約の情報不足に帰属**できる。
- **off-contract イベント 0 件**（全 5 周で著者役のツール使用 0・生データ参照なし）。
- L0-s の成功条件（契約仕様の草案が書ける程度の観測の collect。周回の成否は
  問わない = §5）は**充足**。

## 1. 実施構成（正本 §4.1 / D1–D7 準拠）

- **著者役**: Sonnet（`claude-sonnet-5`）。周回ごとに新規エージェント
  （ステートレス）。可視ペイロード = 著述契約 v0 + 課題文 + 前周の自作
  score/intent + 前周の差分報告のみ（D2）。ツール使用禁止を指示で強制し、
  全周回で使用 0 を確認。
- **ランナー役**: Sonnet。受理 pin → 記号検証 → 決定論演奏・抽出・比較 →
  構造化報告生成 → 台帳記帳。著者役には構造化報告のみ返す（D6。生データ・
  ポインタ非提示）。
- **判定器**: key/brightness = R0 roundtrip（score-adherence は凍結軸表
  `frozen/eval_control_profile.yaml` を注入した評価用コピーで実行 = D7 遮断）。
  構造 = AR4 `svprpe observe`（canonical `frozen/section_map.json` との列一致）。
- **導出器**: score.yaml → observe 実入力一式（wav / performance_package /
  identity_manifest）の自動導出は**リポジトリに存在しなかった**ため、
  `examples/l0s_spike/scripts/measure_round.py` として新設（§5 参照）。

## 2. 周回台帳サマリー（pin の正 = ledger.yaml）

| 周回 | 主変更 | 記号検証 | key | brightness | structure observed（分割数） |
|---|---|---|---|---|---|
| 陽性対照 1 | 人手 6/10/6 bars, bpm 96 | pass | preserved | preserved | 6 分割 |
| 陽性対照 2 | 全セクション短縮 2/3/2 bars (~17.5s) | pass | preserved | preserved | **exact_match**（3） |
| 1 | 初稿 8/16/8 bars, bpm 78 (~98.5s) | pass | preserved | preserved | 8 分割 |
| 2 | chorus 16→8（長さ仮説） | pass | preserved | preserved | 6 分割 |
| 3 | 単一和音反復（和声仮説） | pass | preserved | preserved | 6 分割（仮説反証） |
| 4 | chorus 8→4（長さ仮説へ回帰） | pass | preserved | preserved | 4 分割 [intro, chorus, chorus, outro] |
| 5 | chorus 4→2（事前宣言の分岐規則） | pass | preserved | preserved | **2 分割 [intro, outro]（縮退）** |

- 全提出は受理時点で pin（score_hash / intent_hash）、合格経路は report_hash、
  各周回の実行系（engine commit / deps lock）と著者側同一性（llm_run_id）を
  台帳に記録。
- 著者役の修正戦略はすべて intent sidecar に事前宣言され（単一変数変更・
  分岐規則・リスク宣言）、周回 5 の変更は周回 4 で宣言した規則の機械的適用
  だった。閉ループの操舵自体は規律的に機能している。

## 3. 観測記録（正本 §4.1 の記録 3 種）

### 3.1 スキーマ理解の失敗点

**観測: ゼロ件**。著者役は 5 周とも初回から pydantic 検証を通る YAML を書いた
（未知キー混入・型誤り・フォーマット逸脱なし。`fixity`/`control_profile` の
禁止も遵守）。著述契約 v0 のスキーマ公開範囲（フィールド表 + 型 + 許容値 +
「列挙外キー禁止」）は、Sonnet 級の著者に対して記号層では十分だった。

- 含意（L0a）: スキーマ文書の粒度は手書きガイド水準で足りる。pydantic 自動
  書き出しの必要性はこのスパイクからは示されなかった（§6 未解決課題への回答）。
- 注意: 検証エラーが一度も発火しなかったため、**エラープロトコルの品質は
  未検証のまま**（下記 3.2）。

### 3.2 検証エラーの表現不足

**観測: 収集不能（発火ゼロ）**。全周回が記号検証を通過したため、validation
エラーリストの表現が修正に足りるかは本スパイクでは観測できなかった。

- 一方、**導出器実装側**で判明した契約欠陥: エンジンには「音を出さない検証だけを
  行う CLI」が存在しない（`load_composition_score` を呼ぶ自作ラッパ
  `validate_score.py` を新設した）。素の pydantic ValidationError は構造化されず
  traceback ごと伝播するため、そのままでは §3 の「機械可読の構造化リスト」契約を
  満たせない。**L0a で `svprpe validate`（構造化エラー出力）を契約側の部品として
  凍結する必要がある**。
- エラープロトコル自体の検証は L0a/L0b で、意図的に invalid な Score を流す
  負例テスト（狙い撃ち negative）で行うべき（M3d の狙い撃ち negative と同型）。

### 3.3 差分報告に必要だった情報（何が無くて詰まったか）

off-contract の生データ参照は 0 件だったが、構造軸の 5 周回から**報告の情報
不足が 3 点**特定された:

1. **境界の時刻情報の欠如**: 報告はラベル列のみで、観測セクションの境界時刻・
   各セクション長を含まない。著者は「どの宣言セクションが割れたか」をラベル列の
   並びから推測するしかなく、周回 2–3 で誤仮説（和声変化原因説）に 1 周を消費した。
   観測セクションの時間範囲があれば、断片が chorus 区間内に集中している事実は
   1 周で確定できた。→ L0a: structure 軸の報告に境界時刻（または各観測
   セクションの秒長）を追加する。
2. **計器の分解能・有効帯域の非開示**: 構造センサーの最小セクション間隔
   （5 秒）と「長い持続区間は内部ダイナミクスで過分割される」挙動は契約に
   含めず、著者は 8→6→6→4→2 の試行でこの応答面を逆算する羽目になり、
   最終周回で縮退側へオーバーシュートした（chorus 実長 12.3s は分割・6.2s は
   吸収 = 可行窓は狭く、その存在を著者は知らされていない）。陽性対照
   （chorus 7.5s で合格）は可行窓の実在を証明している。→ L0a: 帯域注釈（D5）を
   拡張し、**軸の値だけでなく計器の分解能・可行域も契約の著述ガイドに載せる**
   （M0/M1 の観測ゲート規律の契約側への延長）。
3. **notes 欄の不使用**: 報告スキーマに `notes: []` を用意したが、ランナーは
   一度も使わなかった。position_match_rate 等の参考値をどこまで報告に載せるかの
   規約が未定義。→ L0a: report.json 正規形の凍結時に notes の使用規約
   （載せてよい参考値の白リスト）を定める。

### 3.4 付随観測（導出器・計器側の発見）

- **構造軸導出器のギャップ**（予告どおり実在）: score.yaml → observe 実入力一式の
  決定論導出は存在せず新設した。判明した制約: IdentityManifest の anchor artifact
  は manifest と同ディレクトリ配下に必要（path 封じ込め）/ `svprpe package` は
  `required: false` の anchor でも実体 + hash 一致を要求 / 空 override の
  ArrangementSpec はパススルーとして妥当。
- **`svprpe perform` CLI は存在しない**（Python API のみ）。L0b の閉ループ自動化
  にはランナー側部品として恒久化が必要。
- **報告フォーマットの軽微な不整合**: 契約 §3 の報告例は YAML、実報告は JSON。
  著者側の混乱は観測されなかったが、L0a の report 正規形凍結で統一する。
- **情報遮断の強制はハーネスレベルでは未担保**: 著者役のツール禁止は指示 +
  自己申告 + ツール使用数の事後確認で運用した（今回 5/5 で使用 0）。L0b の
  自動閉ループでは、ツールを物理的に持たない実行形態を検討する（D2 の機械的
  強制）。

## 4. §5 判定と L0a への引き継ぎ

- **停止条件**: 非発動（記号検証 5/5 通過）。D3（Score 直書き）の見直しは不要。
- **L0-s 成功条件**: 充足（観測 3 種 + 付随観測を収集済み）。
- **L0a 契約凍結への材料**（優先順）:
  1. `svprpe validate`（構造化エラー出力の記号検証 CLI）の新設と凍結
  2. structure 軸差分報告への境界時刻/セクション長の追加（D6 の粒度改訂）
  3. 計器分解能・可行域の著述ガイドへの編入（D5 の契約側拡張）
  4. report.json 正規形の凍結（JSON 統一・notes 使用規約）
  5. 観測経路導出器（measure_round.py 相当）の正式部品化
- **L0b への注意**: 構造軸の「改善」順序（Pareto 述語用のラベル列距離）が未定義。
  分割数の単調減少は改善に見えたが縮退へ突き抜けた（8→6→6→4→2）。距離定義は
  「目標列との編集距離」等で事前登録する必要がある。

## 5. 成果物一覧

| 種別 | パス |
|---|---|
| 事前登録課題 + 3 チェック | `examples/l0s_spike/task.md` |
| 著述契約 v0 | `examples/l0s_spike/contract.md` |
| 凍結物（canonical 列 / 評価軸表 / ArrangementSpec） | `examples/l0s_spike/frozen/` |
| 記号検証ゲート | `examples/l0s_spike/scripts/validate_score.py` |
| 観測経路導出器 + 報告生成 | `examples/l0s_spike/scripts/measure_round.py` |
| 陽性対照 Score | `examples/l0s_spike/positive_control/score.yaml` |
| 周回成果物（score/intent/validation/report × 5） | `examples/l0s_spike/rounds/round{1..5}/` |
| スパイク台帳（全 pin の正） | `examples/l0s_spike/ledger.yaml` |
| 依存 lock | `examples/l0s_spike/deps/pip_freeze.txt` |
