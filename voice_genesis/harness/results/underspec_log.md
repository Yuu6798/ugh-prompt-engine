# Underspecification Log — UGH Voice Genesis Engine v0.2 仮想テスト

設計書「UGH Voice Genesis Engine v0.2」の文章のみから R0 / Phase 0 bench /
Grip Matrix を再実装する過程で、記述だけでは値・式・構造を一意に決定できな
かった箇所を列挙する。コード中の `[UNDERSPEC-n]` コメントと対応する。

凡例: §番号は設計書の節番号。「自分が置いた仮定」は実装で採用した具体的な
選択とその根拠（可能な場合は実測に基づく再校正の経緯も含む）。

| # | § | 欠落内容 | 自分が置いた仮定 |
|---|---|---|---|
| 1 | §3.1 | 応答関数 `breathiness=f(pitch,intensity,register)` 等に現れる `intensity` が、VoiceGenome スキーマ（§3.3）のどのフィールドにも存在しない。静的 Genome 値なのか演奏時パラメータなのか不明。 | `intensity` は Genome の外にある演奏時パラメータ（MIDI velocity 相当、0.0-1.0、基準値 0.7）として `render_note()` の引数に持たせた。Genome には含めない。 |
| 2 | §3.1 | `harmonic_tilt = h(pitch, intensity, source_mode)` の `source_mode` が本文中どこにも定義されていない（値域・意味とも記述なし）。 | Genome 内の記述的タグ（`"modal"` / `"breathy"` / `"pressed"`）とし、tilt の pitch/intensity 感度に対する乗数（`source_mode_tilt_gain`）として実装した。本 VT では `voice_A="modal"`, `voice_B="breathy"` のみ使用。 |
| 3 | §4.3 | 「高次減衰」（spectral tilt に加えて高次倍音がさらに減衰する挙動）の折れ点・傾きの数値が与えられていない。 | 二区間モデル: 倍音番号 k<=8 は基本 tilt に従い、k>8 は追加で `-0.22 dB/harmonic` の減衰を加える。 |
| 4 | §4.3 | フォルマント包絡フィルタの母音 /a/ 相当の周波数・帯域幅の具体値が与えられていない（「3-4 フォルマント」とのみ記述）。 | 標準的な音響音声学の /a/ F1-F4 概算値（800/1150/2900/3900 Hz、帯域幅 80/90/120/130 Hz）を採用。4 フォルマント構成。 |
| 5 | §4.3 | 「高音域 F1 追従（ソプラノ式フォルマントチューニング）」の開始閾値・追従先・遷移形状の式が与えられていない。 | F0 が F1_base の 1.05 倍を超えたところからシグモイド遷移（幅 = F1_base の 15%）で開始し、追従先 = F0 × 0.95 へブレンド、F1 は上方向にのみ動く制約を付けた。 |
| 6 | §3.1 / §3.3 | `breathiness` の register 別ゲイン（chest/mix/head/falsetto/whistle）の数値が与えられていない。 | 初回は chest=0〜whistle=2.2 相当の値を置いたところ、**breathiness が 1.0 を超え（ノイズ RMS が倍音 RMS を上回り）高音域で周期性が事実上消滅し、自前 F0 推定器のオクターブ誤りを誘発する縮退が実測された**。設計書に明記のない制約として「register 項単独では breathiness < 1.0 に収まるべき」を自分で課し、chest=0〜whistle=0.50（voice_A）/ 0.75（voice_B）へ再校正。`breathiness()` にも安全クランプ（0.95 上限）を追加した。 |
| 7 | §3.3 | `chest_to_mix_midi` 等の 4 境界の具体値、および `transition_width` の値が与えられていない（フィールド名の例示のみ）。 | C2-C7 全域に 4 境界を概ね等間隔に配置（52/62/74/88）、`transition_width=3` 半音（全境界共通）とした。 |
| 8 | §4.3 | vibrato rate/depth、jitter の量の数値が与えられていない（機能カテゴリの記述のみ）。 | vibrato_rate=5.5Hz, vibrato_depth=45 cents（voice_A）、jitter は seed 固定の乱数ウォーク（周期比 0.6%相当のスケール定数）とした。 |
| 9 | §3.1 | `register_mix = r(pitch, intensity, transition_state)` の `intensity` と `transition_state` の効果式が無い。 | 本 VT は 1 音・定常母音ロングトーンのみが対象のため、`register_mix` は pitch のみに依存する memoryless 関数として実装（`transition_state`＝履歴は導入しない）。 |
| 10 | §3.1 | `formant_shift = g(phoneme, pitch, intensity)` の `intensity` の効果式が無い。 | R0 では intensity の直接効果は未実装（phoneme 固定=/a/ + pitch による F1 追従のみ）。 |
| 11 | §4.3 | フォルマント包絡フィルタの具体的な実装形（極数・並列/直列・重み付け）が指定されていない。 | 標準的な並列 2 極共振近似（Lorentzian 型 `1/sqrt(1+((f²-Fi²)/(f·Bi))²)`）を各フォルマントで計算し線形振幅を加算する parallel formant bank を採用。 |
| 12 | §4.3 | ノートのアタック/リリース処理、出力レベル正規化の方針が記述されていない（「1音・母音ロングトーン」とのみ）。 | 30ms の線形フェードイン/アウトでクリック回避、出力全体を目標 RMS=0.1 に正規化。 |
| 13 | §9 (VT-2 指示文) | 自前 F0 推定器のアルゴリズムは「例: HPS または自己相関」とのみ例示され一意でない。 | 当初 HPS（Harmonic Product Spectrum, n_harmonics=5）で実装したが、本合成器はフォルマントフィルタが基音を強く減衰させる（フォルマントから遠い低音域ほど）ため missing-fundamental 現象が生じ、低音域で顕著なオクターブ誤りを起こした。時間領域の YIN 式累積平均正規化差分関数に切り替え、さらに（a）真の周期が探索範囲の境界ちょうどに来る極端高音域（C7 は sr=22050 で周期 10.5 サンプルしかない）で境界点が「局所最小」判定から漏れる不具合を修正し、（b）絶対閾値を一度も下回らない場合のフォールバックを「グローバル最小の相対 1.5 倍以内にある局所最小のうち最小ラグのもの」を選ぶ規則に変更してオクターブ誤りを抑制した。voice_A・3半音刻み21ノートでの最終結果は最大絶対誤差 50.4 cents（詳細は `bench_f0.json`）。 |
| 14 | §7.2 (VT-3 指示文でも明記) | grip 定義の z-score 母集団（sweep 内 / probe 内 / 全体）が指定されていない。 | 2 通り実装し両方を `grip_report.json` に記録: (a) `sweep_wide` = 5 sweep点×5 probe=25 セルをプールして正規化、(b) `per_note` = probe ノートごとにその sweep 5 点だけで正規化。軸により grip_ratio の符号・大小関係が変わることを実測した（詳細は grip_report.json 参照）。 |
| 15 | §7.2 (特徴抽出) | 「spectral tilt（ハーモニック振幅の回帰勾配 or 低域/高域エネルギー比）」は 2 択が併記されており一意でない。 | 回帰勾配（各倍音ピーク振幅の dB 値を log2(harmonic index) に対して線形回帰した傾き）を採用。 |
| 16 | §7.2 (特徴抽出) | 「HNR 近似（harmonic band エネルギー vs noise band エネルギー）」の帯域定義（相対帯域幅・解析帯域上限）が与えられていない。 | 各倍音 ±1.5%相対帯域を harmonic band、0-8000Hz 解析帯域内の残りを noise band とした。 |
| 17 | §7.1/§10.1 (VT-3 指示文) | Grip Matrix の probe レンダ音の長さが指定されていない。 | VT-1 と同じ 1.5 秒を採用（統一のため）。 |

## 実測で得られた副次的な発見（instrument-validity caveat）

- **VT-3 の HNR 測定は grip の side-effect 判定でほぼ常に支配的側特徴として現れた**（breathiness / formant_scale / spectral_tilt の 3 軸で `dominant_side_feature = hnr_db`）。これは物理的な軸間干渉（設計書 §7.2 が既に警告する声帯物理由来の絡み）である可能性と、本 VT の HNR 近似（倍音周辺の固定相対帯域窓）がフォルマント/tilt の変化それ自体で harmonic-band/noise-band の切り分けが動いてしまう計器アーティファクトである可能性の、両方が区別できていない。設計書 §7.3 が roundtrip/novelty について指摘する「共有計器問題」と同型の懸念が grip 側にも存在しうることを示す実測結果として記録する。
- **vibrato_depth 軸の C3 probe（最低音）× 高 vibrato_depth sweep 点で、自前 F0 推定器がまれに約 3 倍音への外れ値を返し（フレーム毎トラックの約 1/5 で発生）、vibrato_depth_cents（F0 軌跡の std）の測定値が異常値化する**ことを確認した（`grip_report.json` の `vibrato_depth` 軸 raw_feature_matrices, row=4 col=0 = 748.79 cents）。低音域×大深度 vibrato の組み合わせにおける自前計器の頑健性限界であり、grip 判定自体の well-posedness を脅かす実例。
