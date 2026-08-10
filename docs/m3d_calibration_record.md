# M3d 校正判定記録 — melody トラック closeout(観測律速)

**日付:** 2026-08-09(User 決裁同日)
**状態:** **closeout** — 比較器校正は不成立(観測律速)。melody トラックを dated 終端する。
**正本:** 実測記録 = `docs/measurements/m3d_2026-08/`(preregistration.md / preregistration_v2.md /
README.md / 各 JSON)。設計 = `docs/DESIGN_M3_melody_comparator.md` §6。
**決裁:** 2026-08-08〜09 の実測(v1 凍結拒否 → v2 スクリーニング終端)を受け、User が
2026-08-09 に closeout(選択肢 a)を承認。

## 1. 判定(軸別・材料別)

| 軸 | 判定 | 根拠 |
|---|---|---|
| contour | **not-calibrated(観測律速)** | v1 measured 3 対で margin +0.212(バー 0.15 超)だが標本が統計的に無効(下記 §2)。分離不能の証拠ではない |
| interval | **not-calibrated(観測律速)** | 同上(margin +0.173) |
| rhythm | **not-calibrated** | v1 measured 3 対で margin +0.108 < 0.15。標本無効につき分離可否も未確定 |

- **校正成立軸: 0**。M3 レジストリ(`tests/fixtures/melody_bench/m3_comparison_registry.yaml`)は
  `evidence_thresholds.status: uncalibrated` / `coverage.floor_status: provisional_until_m3d` の
  まま**不変更で凍結しない**(一方向規則維持・凍結値の発明をしない)。比較器の evidence は
  恒久に "none"(正直な未校正)
- material 別会計: synthetic 診断対は v1 で全数 not_comparable(margin 全 null)。
  「synth は診断専用・校正へ不影響」の事前登録どおり会計済み
- **適用範囲宣言: なし**(いかなる帯域・素材にも校正済み閾値は存在しない)

## 2. 根拠となる実測(2 回の事前登録試行がいずれも fail-closed ガードで終端)

### v1(盲選定 98 対・#252 事前登録)
- run×2 完走(pins preflight 済み・repeats **bit 一致 tuning 66/66**(holdout 32 行は
  `holdout_locked_until_frozen` ロックマーカーの同一性であり抽出は実行されていない。
  詳細 = `docs/measurements/m3d_2026-08/run_bit_identity.json`))→ evaluate が凍結提案を
  **`rejected_positive_not_comparable` で拒否**。real tuning positive 46/48・negative 11/12 が
  M1 観測ゲート `phrase_count 1 < min 2` で not_comparable(margin 表は measured 3 対のみ由来)。
  なお、この 66/66 は「run1/run2 間で pair 単位の出力が完全一致する」ことの会計であり、
  66 行それぞれが M3 比較器チェーンの**どこまで**実行されたかとは独立の主張である
  (実行段階別の内訳・強度差は §3 副産物節を参照 — 段階別会計は Codex レビュー #255
  第 11 巡 BB1 で是正)
- 診断(`notcomparable_diagnosis.json`・アノテーション突合): 素材特性(真の 1 フレーズ)
  6 clip + **crepe voicing false-alarm によるフレーズ融合**(アノテーション 2〜4 フレーズ →
  crepe 観測 1)4 clip の複合。変形 b 側の追加劣化あり。M2d 誤差モデル
  (`docs/m2_error_model.md`: voicing が系統的弱点軸)と整合

### v2(観測ゲート・スクリーニング選定・User 承認の再事前登録)
- S1 = vocadito 40 clip 全数 census: **sufficient 13/40**(insufficient 27 は全件
  `phrase_count` 単独——ボトルネックの単一性を確定)
- S2 = 校正に必須の 4 変形(pitch +3/−5・rate 0.87/1.12)で各 6〜7 clip 脱落 →
  **全 5 本通過の survivor は 3/40**(vocadito_1/8/18)
- 分割式で tuning 2 / holdout 1 → 事前登録停止条件(tuning≥6・holdout≥3)に両抵触・
  builder fail-closed。**run 未実施・v1/v2 とも holdout 検証権は未消費**

**結論の言い換え:** 「clean lead 帯でも比較器が弁別できない」(設計書 §6.3 の全滅)では**なく**、
「crepe_direct + 現行 M1 観測ゲート(min_phrase_count=2)の下では、校正に必要な規模の実声
標本が構造的に観測を通らない」。弁別性については肯定も否定もできない(unmeasurable)。

## 3. 帰結(下流の確定)

- **M4 experimental anchor: 不点灯が恒久確定**(G1 未校正が全経路を止める設計のまま。
  `recast/experimental.py` は不変更・evidence "none" 経路が正)
- **L0c(L 系列旋律軸): 非解禁 = L 系列は非旋律軸で完結**(#245〜#250 が終端)
- **G2 帯域語彙是正: moot 化**(M4 不点灯により誤適用経路が発生しない)。ただし将来の
  再開に備え設計判定を記録する: **処方 = 素材種束縛**(recast `BackendRef` に素材種宣言を
  追加し、G2 を「band ∈ 校正済み集合 かつ material が registry の適用範囲宣言に一致」の
  二条件へ強化。語彙細分は凍結済み `melody/routing.INPUT_KINDS` への波及が過大で不採用。
  一次データ = v1 の material 別会計 + M2 の S-direct fail / V-direct pass 分裂)
- **M1-real Go バー(Suno stem 経路): moot 化**(melody トラック終端のため。素材も未回収の
  まま)。v2 S1 census が実素材への観測ゲート大規模適用の初 dated 記録として残る
- 副産物(閉じた事実・段階別会計に是正: Codex レビュー #255 第 11 巡 BB1): repeats
  bit 一致 **tuning 66/66** × 2 round が実確立したのは「tuning 66 行全件が
  crepe 抽出 → M1 観測ゲート判定までを決定論的に再現した」ことである。ただし
  66 行のうち **M3 比較器チェーン(crepe 抽出 → 表現 → NW 整列 → 軸類似)を最後
  まで完走したのは 4 行のみ**で、残り 62 行は途中で `not_comparable` 停止して
  いる(内訳は `run1.json` の `comparison.reasons`/`comparison.axes` から機械
  集計: 観測ゲート `observation_gate_insufficient_*` 止まり 60 行・被覆/整列
  ゲート `insufficient_overlap` 止まり 2 行・全チェーン到達(軸類似まで到達し
  `axes` に数値がある)4 行 = 60+2+4=66)。旧稿の「軌跡レベル決定論を実確立
  した(M2d 残課題の消化)」は、この段階差を無視して 66 行すべてが全チェーン
  を走ったかのように読める過大な主張だった。**正確な主張強度は「全チェーン
  n=4・抽出+観測ゲート n=66 の bit 一致」の段階別会計**であり、抽出+観測
  ゲート段の決定論は 66 サンプルで強く実証されている一方、全チェーンの決定論
  は 4 サンプルの弱い実証にとどまる(M2d 残課題は未完全消化)。holdout 32 行は
  `holdout_locked_until_frozen` ロックマーカー({split, status} の 2 キーのみ)
  の bit 一致(32/32)であり、抽出・比較チェーンは実行されていない — 調整前の
  「98/98」表記はロックマーカー行を含む pair 単位比較の数で、実行チェーンの
  決定論としては tuning 66/66(段階別内訳: 全チェーン 4・全チェーン未到達 62)
  が正確(是正: Codex レビュー #255 第 2 巡 N3、段階別会計は第 11 巡 BB1)。
  校正とは独立に有効な計器性質

## 4. 再入条件(これ以外での再開はしない)

**M0/M1 観測ゲート意味論の再設計を新トラックとして事前登録する場合のみ**。候補は
(a) 単一フレーズ比較の解禁(比較器のフレーズ意味論・負例弁別の再検証を伴う)、
(b) crepe voicing 後処理による無声ギャップ復元(新規計器検証を伴う)。いずれも
M0/M1 凍結値の再登録級であり、着手はロードマップ優先度の User 判断。一次資料 =
`docs/measurements/m3d_2026-08/screening_v2.json`(40 clip census)と
`notcomparable_diagnosis.json`(アノテーション突合)。

## 5. 記録の所在

| commit | 内容 |
|---|---|
| `af94398` | v1 tuning 実測記録(run×2・bit 一致・凍結拒否・診断・provisioning) |
| `aac2bd9` | v2 再事前登録(規則の実測前 commit・順序証明) |
| `ea69395` | v2 スクリーニング census + スクリーナ/builder v2 経路 + synth specs v2 |

運用注記: 実行基盤のバックグラウンドタスク打ち切り(≈4200 s)により v1 run1 は 3 回
失敗後、セッション分離デタッチ + 成果物ベース完了判定で完走(同期実行 3 点文言からの
意図的逸脱・`docs/measurements/m3d_2026-08/run_attempts.log` に全試行記録)。

順序証明の保存条件(Codex レビュー第 7 巡 X2・部分採用): 本記録の git 祖先証明
(`af94398` → `aac2bd9` → `ea69395` → …)はブランチ実履歴で機械検証可能
(`git merge-base --is-ancestor aac2bd9 ea69395` = 真)であり、**マージ commit
方式のマージでのみ main 履歴に保存される**。squash マージは祖先関係を潰し、
本証明を「PR 時点の履歴に存在したという attestation(本注記 + PR #255 のレビュー
記録)」へ格下げする。よって PR #255 は squash 不可(PR 本文にも明記済み)。なお
外部レビュー器が参照した `21bbe5c` / `8e578293` は本リポジトリに存在しない合成
commit(squash プレビュー)であり、ブランチ実履歴の反証にはならない。
