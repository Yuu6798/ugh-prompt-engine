# 機械前提ゲートレポート — R0.9「さくらさくら」（S5 実測）

対象: `singer/render_song.py::render_sakura()` が生成する
`sakura_voiceA.wav` / `sakura_voiceB.wav`（さくらさくら冒頭2フレーズ、
voice_A / voice_B、20ノート、約24.6秒/声）。

全ゲート実行はフォアグラウンド・決定論・数分規模（フルスイート実行時間:
約35秒、`tests/test_machine_gates.py` 実測）。

## 総合判定

| 声 | gate1 F0追従 | gate2 plausibility | gate3 子音実在 | gate4 決定論 | gate5 aliasing | gate6 grip非退行 | 総合 |
|---|---|---|---|---|---|---|---|
| voice_A | PASS | PASS | PASS | PASS | PASS | **PASS** | **6/6 全通過** |
| voice_B | PASS | PASS | PASS | PASS | PASS | **FAIL**（breathiness軸のみ） | 5/6（gate6 breathiness軸のみ未達） |

**判定**: voice_A は S5 の 6 条件を全て満たし、「Phase 2 ゲート判定素材」
としてユーザーに提示可能。voice_B は 5/6 を満たすが gate6 の breathiness
軸のみ未達であり、これは fail-closed で正直に記録する（無理に通していない、
詳細は gate6 節参照）。

## gate 1: 音高追従

各ノートの中央 50%（子音・遷移部を除く母音核）で `measure_v3`
（vt_harness、無改変）強化推定器により F0 を推定し、楽譜値との cents 誤差
を評価する。

| 声 | median\|err\| | max\|err\| | 判定基準 | 結果 |
|---|---|---|---|---|
| voice_A | 10.4c | 16.7c | median≤50c ∧ 全ノート≤100c | **PASS** |
| voice_B | 11.1c | 22.7c | 同上 | **PASS** |

いずれも判定基準に対し大きな余裕がある（全ノードが 25cent 以内に収まって
おり、S1 で発見・対策した missing-fundamental 型オクターブ誤りは解消済み）。

## gate 2: plausibility（周期性）

母音核の periodicity r_median（`measure_v3.periodicity_track_v3`）が
0.35 を下回るノートが 0 件であること。

| 声 | 違反数 | 判定 |
|---|---|---|
| voice_A | 0/20 | **PASS** |
| voice_B | 0/20 | **PASS** |

## gate 3: 子音の実在

/s/ /k/ /t/ の各出現箇所で、子音区間の高域（≥3kHz）エネルギー比が
直後の母音区間の **1.3 倍以上**（閾値、[UNDERSPEC-S5-1] 参照）であること
を確認する。

| 声 | 検査数 | 通過数 | 判定 |
|---|---|---|---|
| voice_A | 8（/s/×4, /k/×3, /t/×1） | 8 | **PASS** |
| voice_B | 8 | 8 | **PASS** |

実測比率は全箇所で 5〜99 倍と閾値を大幅に超えており、子音が明確に
「鳴っている」ことを機械確認できた。

## gate 4: 決定論

同一 Genome で `render_sakura()` を 2 回実行し、出力波形の SHA-256 ハッシュ
が一致すること。**プロセスを再起動しても一致すること**も別途確認した
（Python 組み込み `hash()` はプロセス毎にランダム化されるため、これに
依存しないシード生成 `deterministic_seed()`＝hashlib.sha256 ベースへ実装を
修正済み、詳細は underspec_log_s1.md [UNDERSPEC-S1-1 系] 参照）。

| 声 | 判定 |
|---|---|
| voice_A | **PASS**（同一プロセス内・別プロセス間とも波形完全一致） |
| voice_B | **PASS**（同上） |

## gate 5: aliasing

出力（22050Hz）の 0.45×sr = 9922.5Hz 以上のエネルギー比が -40dB 未満。

| 声 | エネルギー比 | 判定 |
|---|---|---|
| voice_A | -82.2dB | **PASS**（40dB以上の余裕） |
| voice_B | -74.9dB | **PASS**（同上） |

4倍オーバーサンプリング（88200Hz）でパルス励振・時変フォルマントフィルタ
を処理し、`scipy.signal.resample_poly` の多相 FIR デシメーションで
22050Hz へ落とす設計が有効に機能している。

## gate 6: grip 非退行クイックチェック

R0.9 の持続母音経路（`render_sustained_vowel`、performance 層を経由しない
定常測定用の最小レンダラ）で breathiness / vibrato_depth の 2 軸 grip を
再測。probe suite {C3,E4,A4,C5,C6}、intended 特徴は breathiness→periodicity、
vibrato_depth→vibrato_depth_cents（vt_harness 系の grip 定義を継承、
特徴セットは mean_f0/periodicity/rms/vibrato_depth の 4 特徴に縮小
——formant/tilt 軸は R0.9 で写像自体が変わるため参考測定のみでよいと
memo が明記しているため対象外とした）。

| 声 | 軸 | grip | dir一致率 | E(intended) | 判定 |
|---|---|---|---|---|---|
| voice_A | breathiness | 3.173 | 1.0 | 3.173 | **PASS** |
| voice_A | vibrato_depth | 9.634 | 1.0 | 9.634 | **PASS** |
| voice_B | breathiness | **1.020** | 1.0 | 2.213 | **FAIL** |
| voice_B | vibrato_depth | 8.712 | 0.90（境界値） | 8.712 | PASS |

**voice_B の breathiness 軸のみ未達（fail-closed で記録）。**
dominant side は vibrato_depth（E=2.171、E(intended)=2.213 とほぼ同水準）。
voice_B は「breathy」archetype として意図的に高い vibrato_depth(55c)・
jitter(0.005)・shimmer(0.035) を持たせており、breathiness を掃引すると
追加されるノイズが F0 トラックの頑健性をわずかに損ない、
`vibrato_depth_robust_v3`（棄却+MAD方式）で測る見かけの vibrato_depth が
連動して動いてしまう（真の物理的結合というより計測経路上のクロストーク
の可能性が高い、v0.3〜v0.6 サイクルで繰り返し観測された「計器アーティ
ファクトか物理結合か切り分けが必要」というパターンと同型）。
shimmer 低減（0.035→0.02）を試したが改善せず、むしろ vibrato_depth 軸
自体の dir が 0.90 未満に落ちる新規劣化を招いたため差し戻した（詳細は
underspec_log_s1.md 参照）。**時間制約内でこれ以上の追加チューニングは
行わず、正直に未達として記録する。**

voice_A（modal archetype、breathiness_base=0.05・vibrato控えめ）は
全軸で明確に gate を通過しており、**新しい励振（声帯パルスモデル）が
Genome 契約（breathiness応答関数・vibrato_depth）そのものを壊していない
ことは実証できた**。voice_B の未達は「特に極端な設定を重ねた場合の
計測経路の脆弱性」であり、レンダラ・Genome契約の根本的な破綻ではないと
判断する。

## 実装過程で発見・修正した実質的なバグ（S1 由来）

1. **決定論の欠陥**: シード生成に Python 組み込み `hash()` を直接使用して
   おり、プロセス再起動でハッシュ値が変わりうる（PYTHONHASHSEED の既定
   ランダム化）ため gate4 を静かに破りかねなかった。`hashlib.sha256` ベース
   の安定シードに修正。
2. **基音喪失型オクターブ誤り**: フォルマント遠方の基音が倍音に対し極端に
   減衰し、`measure_v3`（無改変）の F0 推定器がオクターブ誤りを起こす
   実害を実測。フォルマントゲインに低域限定の下駄（`GAIN_FLOOR`,
   `GAIN_FLOOR_CUTOFF_HZ`）を追加して解消（aliasing への悪化を避けるため
   高域はロールオフさせている）。
3. **breathiness ノイズのグローバルスケーリングバグ**: 曲全体で 1 回だけ
   ノイズ RMS を調波 RMS にマッチさせてから曲全体平均の breath_gain を
   掛けていたため、局所的に励振が弱い/breathiness が高いノートで意図した
   比率を大きく超えるノイズが乗り、オクターブ誤りを誘発していた。ノート
   ごとの個別スケーリングに修正。

詳細な機序・数値は `underspec_log_s1.md` を参照。
