# S5 Cross-Song Report — 第二の曲による再利用実証 + 証明書衛生

- 日付: 2026-08-13
- 仕様: コーディネーター指示メッセージそのもの（新規メモなし。補充は
  `underspec_log_s5.md`）
- 前提: S4 耳判定成立（`results_s4/s4_gate_record.md`）

## 1. 第二の曲: 「うみ」

パブリックドメイン文部省唱歌「うみ」（1941年、作詞林柳波・作曲井上武士）
冒頭3フレーズ「うみは / ひろいな / おおきいな」を新設 `singer/score_umi.py`
に定義した（`score.py` は無改変）。

**選定理由**:
- 音素インベントリ確認: う/み/は/ひ/ろ/い/な/お/き の onset は
  {None, m, h, r, n, k} で全て `phoneme_jp.CONSONANTS` 内
  （`kana_to_morae()` で分解確認済み、エラーなし）
- 「さくらさくら」（都節音階・4音構成の陰旋法）と対照的な長音階寄りの
  旋律で、cross-song 比較において「曲固有の旋律的個性」と「歌手固有の
  声質」を分離しやすい
- 3フレーズ・1モーラ1ノート・メリスマ/促音なし（score.py と同じ制約）

**採譜の限定事項** [UNDERSPEC-S5-1]: 実装者の記憶に基づく再構成であり、
検証済みの原典採譜ではない（`score.py` 冒頭コメントの「さくらさくら」の
先例と同じ位置づけ）。テンポ 88 BPM も凍結 assumption。

## 2. cross-song レンダ + 曲単位の機械ゲート

`render_song.render_sakura()` に `notes`/`tempo_bpm` パラメータを追加した
（既定 None で従来通り「さくらさくら」を描画、既存呼び出しは完全に非退行。
関数名は歴史的経緯でそのまま）。genesis3・voice_C の2歌手 × {さくら,うみ}
の計4レンダを実施（さくら側は既存 `results_s4/sakura_genesis3.wav`・
`results_s2/sakura_voiceC.wav` を参照、うみ側を新規出力）。

genesis3 の Genome は `results_s4/lineage_genesis3.json` から復元
（`out_of_physio_range=False` を再確認）。

### 曲単位ゲート（うみ、gate1/gate2/gate3/gate5。gate6-v2は歌手単位で既得のため対象外）

| 歌手 | gate1 (median/max cents) | gate2 | gate3 (子音: き のみ該当) | gate5 (dB) | 全通過 |
|---|---|---|---|---|---|
| genesis3 | ✓ (4.86 / 13.96) | ✓ | ✓ (1/1) | ✓ (-62.6) | ✓ |
| voice_C | ✓ (15.52 / 26.51) | ✓ | ✓ (1/1) | ✓ (-53.5) | ✓ |

gate3 は `gate_checks.gate3_consonant_existence` の `target_onsets`
（s/k/t）が「うみ」の子音インベントリ（h/r/n/m/k）と自然に交差した結果
「き」（k, おおきいな内）のみが該当インスタンスとなった。これは
「新曲の子音インベントリで再定義」の要求を、既存関数の無改変再利用で
自動的に満たす形になっている（s/tが存在しない曲では判定対象がkのみに
絞られる、という既存実装の汎用性による）。

## 3. cross-song identity 計測（Experiment B 縮約）

E1/E2（`identity_metrics.py`、rms次元除外の頑健化適用済み）で、各歌手の
song 内フレーズ平均ベクトルを算出し、以下を計測:

| 距離 | 定義 | E1 | E2 |
|---|---|---|---|
| a_genesis3 | within-singer/cross-song（genesis3: さくら vs うみ） | 0.1662 | 0.0835 |
| a_voiceC | within-singer/cross-song（voice_C: さくら vs うみ） | 0.0429 | 0.0354 |
| b_sakura | cross-singer/same-song（さくら: genesis3 vs voice_C） | 0.1333 | 0.0949 |
| b_umi | cross-singer/same-song（うみ: genesis3 vs voice_C） | 0.2196 | 0.2244 |

### identity 保存判定（(a) < (b) の全組み合わせ、[UNDERSPEC-S5-2] 参照）

| 歌手 | vs 曲 | E1 | E2 |
|---|---|---|---|
| genesis3 | vs sakura | **0.1662 < 0.1333 → 不成立** | 0.0835 < 0.0949 → 成立 |
| genesis3 | vs umi | 0.1662 < 0.2196 → 成立 | 0.0835 < 0.2244 → 成立 |
| voiceC | vs sakura | 0.0429 < 0.1333 → 成立 | 0.0354 < 0.0949 → 成立 |
| voiceC | vs umi | 0.0429 < 0.2196 → 成立 | 0.0354 < 0.2244 → 成立 |

**判定: 8チェック中7成立、1不成立（`ALL IDENTITY PRESERVATION CHECKS PASS = False`）**。
唯一の不成立は genesis3 の E1（measure_v3系）における within-singer/
cross-song 距離(0.1662)が、cross-singer/same-song(さくら)の距離(0.1333)を
僅かに（margin -0.033）上回るケース。voice_C は4チェック全て成立し、
genesis3 の E2 系および umi 側の比較は全て成立している。

**解釈**: genesis3 は多世代探索（S4）で「両親から複合JND距離≥2.0」を
満たすよう物理事前分布内を大きく動いた個体であり、voice_C（探索の起点
そのもの）と比べて曲間（さくら↔うみ、音域・旋律輪郭が大きく異なる）での
tract/声質特徴の実現値のブレが本質的に大きい可能性がある。これは
「genesis3 の声道パラメータがより極端な領域にあるため、曲によって
formant/tilt の実現され方に敏感」という仮説と整合する（S2/S3/S4で
繰り返し観測された「多次元・多条件での挙動は単純な外挿では読めない」
という教訓の一例）。identity保存が完全ではない、という正直な結果として
報告する（無理に「成立」と判定しない）。

## 4. JND 会計（同一歌手の曲間、tract/声質系の安定性）

各歌手内で さくら vs うみ の母音核（フレーズ先頭2ノート×3フレーズ分の
重複区間）を比較した中央値 JND（S2/S3と同一の `measure_v3` 6特徴 ÷
v0.3 REF_SCALE）:

| 特徴 | genesis3 (曲間JND) | voice_C (曲間JND) |
|---|---|---|
| mean_f0 | 11.478 | 10.817 |
| formant_centroid | 3.734 | 3.368 |
| source_tilt | 4.945 | 4.879 |
| periodicity | 1.119 | 0.557 |
| rms | 3.524 | 4.104 |
| vibrato_depth | 0.540 | 0.515 |

mean_f0 は曲の旋律・音域差そのものを反映するため大きくて当然（identity
指標ではない）。tract 系（formant_centroid・source_tilt）はどちらの歌手も
同程度の中程度の曲間変動（3.4〜4.9 JND）を示し、genesis3 が voice_C より
著しく不安定というわけではない——§3で見つかった E1 の不成立は formant_
centroid/source_tilt 単体の不安定性というより、6特徴の複合的な
z-score/コサイン距離空間での効果と考えられる。

## 5. 証明書衛生: voice_C / voice_D の gate6-v2 正式再監査

| 歌手 | gate6-v2 判定 | breathiness grip | vibrato grip | provenance |
|---|---|---|---|---|
| **voice_C** | **✓ 合格** | 3.663 | 6.024 | `measured (score-informed QC)` |
| **voice_D** | **✗ 不合格** | 2.741 | 5.242 | `measured (score-informed QC)` |

（S4 `safe_box_v2.md` の実測値と一致することを本サイクルで再確認済み。）

**註記（`s2_gate_record.md` は無改変。本報告書側にのみ記す）**: S2 の
voice_A/voice_B 識別成立の耳判定（`results_s1/phase2_gate_record.md`）
自体は人間聴取に基づく判定であり、gate6（機械前提ゲート）の合否とは
独立した証拠であるため**有効なまま**である。ただし **gate6-v2（score-
informed QC）による品質証明は voice_C のみが保持しており、voice_D は
保持していない**。voice_D を今後「機械ゲート込みで検証済み」の個体として
引用する場合は、この gate6-v2 不合格を明記すること。

## 総括

- 「うみ」を第二の曲として cross-song 実証を実施。曲単位ゲート
  （gate1/2/3/5）は genesis3・voice_C とも全通過
- identity 保存判定は 8チェック中7成立。唯一の不成立（genesis3・E1・
  vs sakura）は僅差であり、genesis3 が探索由来の個体であることに起因する
  可能性を指摘したが、確証には至っていない（次サイクルの検証候補）
- JND会計では両歌手とも同程度の曲間 tract 安定性を示し、genesis3が
  voice_Cより明確に不安定とは言えない
- 証明書衛生: voice_C は gate6-v2 合格、voice_D は不合格を正式記録。
  S2 の耳判定の有効性とは独立の事実として明記した
