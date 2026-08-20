# DESIGN Plan B PoC — Identity × Performance Skill 分離の実測ハーネス

- 起草・実行: 2026-08-20（Claude セッション。User 指示「プランを実行検証する」）
- 一次資料: User 提供プラン `VoiceGenesis_PLAN_B_identity_performance_separation.md`
- **run 8 からの独立宣言**: 本トラックは run 8（り→ん破綻の専用調査・別セッション）
  から独立した単独タスクであり、run 8 の設計・実測・判定に一切影響を与えない。
  逆に run 8 の結果を待たずに走る（プラン §9 の切替フローに従うなら「Run 8 の
  結果を見てから発動」だが、本セッションでは**発動条件の判定ではなく計器の
  先行整備**として走らせている）
- 実装: [`.`](.)（`pb_*.py`）/ 実測記録: [`results_pb0/`](results_pb0/)

---

## 0. 本 PoC が答える問いと、答えない問い

プラン §1 の狙いは「現行モデルで一緒に学習されている『誰の声か』と『どう歌うか』を、
既存資産で一度外部で分離し、分離可能性を実音で証明する」ことである。

本セッションで**答えられる問い**:

- Q-A: Identity（テクスチャ）と Performance（制御）を、**テクスチャの混入経路が
  構造的に存在しない**形で実装できるか
- Q-B: 成分（F0 / duration / energy / release）を独立に ON/OFF し、どれが効いたかを
  **機械的に帰属**できるか
- Q-C: 同一入力 + 同一制御で**再現**するか
- Q-D: terminal release failure（TRF）を測る軸に**弁別力**があるか（用量反応・
  材質依存・交絡の実測）

本セッションで**答えられない問い**（実資産 + 人間の耳が律速）:

- Q-E: リツの声としての聴感が保たれるか（プラン受入 1）
- Q-F: 実 DiffSinger の り→ん破綻が実際に改善するか（同 2）
- Q-G: PJS 実歌唱の Performance が効くか（P0 は代替ドナーで置換）

Q-E/F/G は本設計で `blocked` として明示的に扱い、**成功に数えない**。

---

## 1. 資産の現実（在庫棚卸し）

| プランが要求する資産 | 本環境での実在 | 対応 |
|---|---|---|
| 波音リツ 強連続音 Ver1.5.1 / A3・F4 VCV bank / oto.ini | **不在**（`oto.ini` 0 件） | 代替ドナーで機構検証 |
| F1.4 VCV unit renderer | 実装は在（`../adapter/`）だが voicebank 実体が要る | 未使用 |
| PJS corpus ver1.1（実歌唱 100 曲・lab） | **不在** | 代替ドナーで機構検証 |
| WORLD（spectral envelope / aperiodicity） | `pyworld` を本セッションで導入（0.3.5） | 使用 |
| 決定論歌唱器（学習フリー・資産不要） | 在（`../../singer/` R0.9） | **代替ドナーの生成源** |

代替ドナー:

```text
Identity 代替   = voice_A（R0.9・テンポ 72・素の歌唱）
Performance 代替 = voice_B（R0.9・テンポ 66・rubato・別 microprosody）
```

題材は `singer/score.py` の「さくらさくら」（凍結スコア）。この曲は
**プラン §3 の probe を 2 つ実際に含む**: フレーズ末 /ri/（「かぎり」）と
フレーズ末 /su/（「みわたす」）。s6 記録が挙げた 2 つの実観測課題
（「り→ん破綻」「みわたす語尾の連続発声」）と同じ綴りの区間が取れる。

**被覆の正直申告**: プラン §3 の held-out のうち `/i/ → end`・`/N/ → end`・
phrase-medial `/ri/` は本題材に**存在しない**。record の `probe_coverage` に
空リストとして機械出力される（存在しないものを合成で埋めない）。

---

## 2. 分離の実装境界（プラン §4 の型化）

```text
IdentityBank      : sp (T,F) / ap (T,F) / unit 境界 / 母音コア / 鼻音包絡
PerformanceTrack  : f0_dev_cents (T,) / unit_durations_s (U,) / energy_db (T,)
                    / ReleaseSpec{window_frac: scalar, taper_db: (R,), hold_core: bool}
```

規約:

1. **Performance 側は 1 次元しか持たない。** energy はフレームごとの
   *スカラ利得*であってスペクトル形状ではない（rank-1）。
   `pb_tracks.assert_no_spectral_payload` が型で強制する
2. **F0 は絶対音高を運ばない。** 各 unit のコア中央値からの逸脱（cent）だけを
   移植する。歌手の音域は identity 的性質なので Performance に含めない
3. **release skill のテクスチャ源は identity 自身のコア**である。Performance から
   来るのは窓長（スカラ）と利得カーブ（1 次元）のみ
4. 合成器は `IdentityBank` と `PerformanceTrack` 以外の入力を持たない

プラン §7.1 の「PJS の声を混ぜただけでは成功扱いしない」を、耳ではなく
**器の形**で担保する設計である。

---

## 3. R0–R4 ラダーと補助段

| ID | トグル | プラン対応 |
|---|---|---|
| R0 | なし | baseline |
| R1 | f0 | PJS F0 のみ |
| R2 | duration | PJS duration のみ |
| R3 | f0 + duration | F0 + duration |
| R4 | f0 + duration + energy + release | 全成分 |
| P0 | — | Performance ドナーそのもの（参照） |

プラン §7.2 は「F0 / duration / energy / release を**別々に**交換し、どれが効いたかを
保持する」と要求するが、R0–R4 だけでは R4 が energy と release を同時に入れるため
この要求を満たせない。よって**補助段**を追加する（事前登録ゲートの対象外・
帰属の読み取り専用）:

| ID | トグル | 目的 |
|---|---|---|
| S1 | energy のみ | energy 単独の効果 |
| S2 | release のみ | release skill 単独の効果 |
| S3 | energy + release | R4 の増分の内訳 |
| S4 | release（`hold_core=False`） | release skill の内訳（コア保持 vs 利得カーブ） |

---

## 4. TRF 軸と事前登録

### 4-1. 軸（総合スコアは作らない）

svp-rpe 側 M3 の規約（`docs/melody_comparator.md`）と同じく、**軸別 evidence のみ**を
出す。probe unit ごとに:

| 軸 | 定義 | 向き |
|---|---|---|
| `nasal_gain_db` | `LSD(コア, 鼻音) − LSD(release 窓, 鼻音)` | + = 鼻音へ寄った（り→ん signature） |
| `drift_db` | `LSD(release 窓, コア)` | + = コアから離れた |
| `f0_sag_cents` | release 窓の中央値 F0 / コアの中央値 F0 | − = ずり下がり |
| `energy_tail_db` | 終端 20% の平均パワー − release 窓頭 | 0 近傍/+ = 連続発声 |
| `nasal_gain_shape_db` | 各フレームを総パワー 1 に正規化してから同上 | 同上（レベル非依存） |
| `drift_shape_db` | 同上 | 同上 |

「コア」は unit の 35–60%（立ち上がり直後の母音の姿）を参照とする。終端側の
変質を参照に含めないため。

計測は合成結果の内部配列ではなく、**合成した wav を WORLD で再解析**して行う
（意図ではなく結果を測る。ボコーダ段で潰れた変化を改善に数えない）。

### 4-2. 事前登録プロトコル

決定ルールは本設計書で先に固定し、**数値だけを R0 baseline から導く**。
`pb_ladder.run()` はコード上の順序でこれを強制する:

```text
R0 を合成 → R0 のみ計測 → freeze_trf_gate(R0) → JSON へ凍結 → R1–R4 を計測 → 判定
```

凍結ルール（`pb_gates`）:

- 主軸 = `nasal_gain_db`、主 probe = フレーズ末 /ri/
- `failure_present` = R0 の主軸 >= 1.0 dB。**False のときは `not_evaluable`** とし、
  改善を主張しない（baseline に破綻が無いのに「改善した」と言わないための門）
- 必要改善量 = `max(1.0 dB, 0.25 × |R0 値|)`
- 副軸回帰: `drift_db` / `energy_tail_db` が R0 比 +1.0 dB を超えないこと
- held-out: 各 held-out probe の主軸が R0 比 +1.0 dB を超えないこと

### 4-3. 陽性対照 / 陰性対照

代替ドナーには り→ん破綻が存在しない。そこで**合成故障**を identity 側へ焼き込む:

```text
終端 unit の末尾 35% で、母音包絡を identity 自身の鼻音包絡へ向けて
指数ランプでブレンド（depth 既定 0.85）+ 利得を据え置き（= 連続発声）
```

- 鼻音テクスチャは **identity 自身の実測**（本題材では「の」の /n/ 頭部）から取る。
  Performance ドナーのテクスチャは 1 バイトも混ざらない
- **これは実 DiffSinger の破綻原因の主張ではない**。TRF 軸に弁別力があるかを測る
  ための陽性対照であり、機構の十分性しか示さない
- **陰性対照** = 同じラダーを故障なしで走らせる。ここで改善が「立ってしまう」なら
  軸が改善を捏造している

---

## 5. 受け入れゲート（プラン §8 の機械化）

| プラン条件 | ゲート | 種別 |
|---|---|---|
| 1. R0/R4 で identity 維持 | G6: 全段で `donor_LSD − identity_LSD >= 1.0 dB` | 機械代理 |
| 1. 同上（聴感） | G-ear | **blocked**（耳律速） |
| 2. R4 が R0 より TRF 改善 | G7: 事前登録ゲート | 代替素材では合成故障に対して |
| 2. 同上（実 TRF） | G-ear | **blocked**（実資産律速） |
| 3. held-out で重大回帰なし | G7-holdout（終端 /su/）+ G8（フレーズ中間 unit） | 機械 |
| 4. どこで改善したか再現可能 | G5（帰属）+ 補助段 S1–S4 | 機械 |
| 5. 同一入力 → 再現性 | G1（同一プロセス 2 回 + 別プロセス 1 回の sha256 一致） | 機械 |
| 6. donor テクスチャ非使用 | G2（構造）+ G3（tripwire）+ G4（実測 LSD 非接近） | 機械 |

G6 が絶対距離ではなく **margin**（donor 距離 − identity 距離）で判定するのは、
合成 → 再解析の往復に床（本ハーネスで ~2.9 dB）があるためで、絶対値の閾値は
床と改善を区別できない。

G3 の tripwire は `PerformanceTrack` をプロキシで包んで合成を回し、
**合成器が実際に読んだ属性名の集合**を記録する。宣言外フィールドへのアクセス、
または 2 次元配列の返却が起きた時点で fail にする。

---

## 6. 実資産経路への接続（本 PoC の出口）

代替と実資産で**コードは分岐しない**。`pb_extract.build_identity_bank` /
`build_performance_track` は `(WorldAnalysis, Unit 列)` だけを取るので、
実資産で追加が要るのは以下だけである:

1. **wav**: リツ VCV バンクを F1.4 レンダラで鳴らした出力（Identity）と、
   PJS 実歌唱（Performance）
2. **Unit 境界**: PJS の lab / リツの oto.ini から
   `pb_tracks.units_from_boundaries(labels, boundaries_s, terminal_flags=...)` へ

`core_frac` の既定（35–85%）は代替素材向けの推定であり、lab に音素境界がある
実資産では厳密値を渡すこと。

**実資産走行での差分**: 合成故障の注入は行わない（`--no-fault`）。実 TRF が
R0 に存在すれば `failure_present=True` になり、G7 が実測ゲートとして機能する。
存在しなければ `not_evaluable` が返り、それは「リツのこの発話に破綻が無い」という
実測結果である。

---

## 7. 逸脱・未解決（申し送り）

1. 代替素材はプラン §3 の held-out を 2/5 しか含まない（`/i/`・`/N/`・
   phrase-medial `/ri/` が不在）。実資産走行では probe 語彙を先に確保すること
2. 合成故障は「鼻音へのスペクトルドリフト + 利得据え置き」の 1 モデルのみ。
   実 TRF が別機序（例: laryngeal / voicing の崩れ）なら本 PoC の陽性対照は
   その機序を代表しない
3. 本 PoC は WORLD 上の制御のみを扱う。プラン §5 Case E（acoustic latent まで
   絡む場合）は本ハーネスの射程外
4. `energy` 移植は identity 自身の unit 内エンベロープを reference の形へ
   **置換**する実装にした（加算は二重計上で発散する = 初回実測で確認）。
   置換は「Performance = energy envelope」の素直な解釈だが、identity 固有の
   強弱の癖まで消す。癖の帰属（identity か performance か）は未決
