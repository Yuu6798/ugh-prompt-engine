# 設計書 — M4（melody anchor の Recast 配線・experimental）

**宛先:** Claude Code（実装）
**発行元:** Cowork（設計・検収）
**前提コミット:** `e244b87`（main、M3 実装 PR #233 マージ済み）
**前提状態:** M3d 校正実測は**未実行**（registry `status: uncalibrated`）。本設計はそれを前提に、起動を registry 状態で機械ゲートする（設計・実装のマージは校正前でも可、**発火は校正後**）。
**スコープ:** M3 比較器の出力を Recast の D-1 語彙（preserved / changed_within_policy / changed_outside_policy / not_observed）へ接続し、`melody` を **experimental anchor** として RecastReport に載せる。**M0〜M3 の凍結値・既存スキーマの変更なし（追加のみ）。**

---

## 0. これが最終駅である（1分）

M1「聞こえるか」→ M2「聞き間違えていないか」→ M3「同じ旋律か」→ **M4「その答えを、編曲の約束（契約）の言葉に翻訳して検収書に載せる」**。
翻訳であって測定ではない。M4 は新しい数値を一切生まない——M3 の evidence を契約に照らして語彙へ写像するだけ。ここに判断ロジックを足し始めたら設計違反である。

## 1. 起動ゲート（全て機械検証・コードに埋める）

| ゲート | 条件 | 不成立時 |
|---|---|---|
| G1 校正 | `m3_comparison_registry.yaml` で **≥1 軸が frozen/calibrated** | anchor は常に `not_observed(reason: comparator_uncalibrated)` |
| G2 帯域 | 比較の両入力が**校正済み帯域**（現状: 単離済み clean lead）内 | `not_observed(reason: band_out_of_validation)` |
| G3 観測 | 両入力が M1 観測ゲート通過 + M3 被覆下限通過 | `not_observed(reason: <gate理由の転記>)` |

**G2 の含意を正直に:** 実運用（Suno フルミックス→demucs stem）の melody anchor は、
**stem 帯の精度実測**（V-fullstack = MedleyDB、または V-remix 方式: vocadito 歌声×自作伴奏
ミックス→分離→抽出→注釈照合）が dated で通るまで**点灯しない**。これは M4 の欠陥では
なく M2 帯域地図の忠実な反映。stem 帯解禁は別の一頁実測設計（M2e）で行う。

## 2. 参照の二形態（何と何を比べるか）

- **score_reference（優先）**: 原曲側 = CompositionScore の旋律を**記号から直接** f0/ノート列
  へ決定論導出（抽出器を通さない＝原曲側の抽出誤差ゼロ）。生成テイク側のみ抽出。
  sidecar-first の本流であり、G2/G3 はテイク側にだけ課せばよい。
- **audio_reference**: 原曲もテイクも音声から抽出。両側に G2/G3。原曲が音声でしか
  存在しない場合の後備。
manifest/project 側は `melody_reference: score | audio` を additive に持つ（既定 score・
score に旋律が TODO のままなら `not_observed(reason: author_input_missing)` — DD-D 準拠）。

## 3. 軸別契約（hard/elastic/free の melody への適用）

PreservationContract の語彙を**軸単位**へ拡張する（additive・新スキーマ版は切らない）:
```yaml
anchors:
  melody:
    status: experimental          # 恒久 experimental ではない・昇格は §6
    axis_policy:                  # M3 の校正済み軸のみ指定可（未校正軸の指定は load 時 fail-closed）
      contour: hard               # 例: 輪郭は不可侵
      interval: elastic           # 音程の細部は揺れてよい
      rhythm: free                # リズム変形は自由（ジャングル化等）
```
### 写像規則（M3 evidence → D-1 語彙・機械的）
| 条件 | anchor 状態 |
|---|---|
| G1–G3 のいずれか不成立 | **not_observed**（理由転記） |
| hard 軸のどれかが evidence **none** | **changed_outside_policy** |
| hard 軸のどれかが **weak**（strong 未満） | **not_observed**（reason: insufficient_evidence — **weak で preserved を主張しない**。保守側へ倒す） |
| 全 hard 軸 strong・elastic 軸に none/weak あり | **changed_within_policy** |
| 全 hard 軸 strong・全 elastic 軸 strong | **preserved** |
free 軸は判定に不参加（報告には併記）。octave_artifact_suspected は理由欄へ必ず転記。

## 4. レポートへの載せ方（experimental の会計分離）

- RecastReport に `experimental_anchors` 節を**新設**し、melody はそこに載せる。
  **既存の被覆集計（verified/violated/not_observed の分母）には算入しない**——
  experimental が本会計を汚すと、既存レポートの意味が M4 マージ日を境に変わってしまう。
- 載せる中身: 語彙判定 + M3 の軸別値・被覆・理由 + provenance（sequence hash・
  比較 registry hash・経路）。**単一同一性スコアは出さない**（恒久禁止・報告層でも）。
- `recast plan` 側: melody anchor が契約にあれば、配送可否診断に「observability 見込み
  （帯域・校正状態）」を1行出す（生成前に「この構成では melody は検収不能」を教える）。

## 5. PR 分割

| PR | 内容 | 受け入れ条件 |
|---|---|---|
| M4a | axis_policy スキーマ + 起動ゲート G1–G3 + 写像規則（純関数） | 写像の全分岐の表駆動テスト。未校正軸指定の fail-closed。既存スキーマ diff ゼロ |
| M4b | score_reference 導出（記号→ノート列・決定論） | 既知 score で手計算一致。TODO 旋律→not_observed |
| M4c | observe/report 統合（experimental 節・会計分離） | 既存レポートの回帰スナップショット不変。melody 追加時のみ experimental 節が現れる |
| M4d | golden path E2E | deterministic backend（perform/ 合成 = **clean lead 帯 = 校正帯域内**）で init→…→report を melody anchor つきで一周。G1 未成立時は not_observed になる分岐も E2E で確認 |

**M4d が回る根拠:** 内蔵演奏者の合成音は clean lead 帯なので、stem 帯未解禁でも
E2E は校正帯域内で完結する。実 Suno テイクでの field trial だけが stem 帯解禁待ち。

## 6. 昇格と将来（実装しない・壊さない）

- experimental → 正式 anchor の昇格は、**実運用 dated 使用 N 回でユーザーの耳との矛盾ゼロ**
  を条件とする **User 決裁**（自動昇格なし）。会計算入もその時。
- 歌詞 anchor・和声 anchor は本設計の対象外（melody の配線パターンを踏襲できる形にだけ
  しておく——axis_policy とゲート機構を melody 専用にハードコードしない）。

## 7. やってはいけないこと

- M4 層での判断ロジック追加（閾値・重み・スコア合成）。M4 は写像のみ。
- experimental anchor の本会計算入。単一同一性スコア（報告層含め恒久禁止）。
- weak evidence からの preserved 主張（保守側へ倒す規則の緩和）。
- stem 帯・フルミックス帯での発火（G2 を외す・緩める）。
- M3d 校正前の G1 バイパス。凍結値（M0〜M3 registry）の変更。
- 既存 RecastReport スキーマの破壊的変更・既存レポートの意味変更。

一文: **M4 は翻訳器である——M3 の「同じ旋律である証拠」を、契約の「守られたか」へ機械的に写す。測れない場所では点灯せず、弱い証拠では約束せず、実験中の灯りは本勘定に混ぜない。**

---

**Closeout 注記（2026-08-09、本文は計画時の記述のまま不変更）**: 上記 §「前提状態」の
「M3d 校正実測は未実行」は 2026-08-09 に M3d closeout（観測律速・校正不成立、
melody トラック dated 終端）へ確定した——G1（M3 registry calibrated）は恒久不成立、
本設計の起動（発火）は行われない。判定・再入条件は
[`m3d_calibration_record.md`](m3d_calibration_record.md) が正。
