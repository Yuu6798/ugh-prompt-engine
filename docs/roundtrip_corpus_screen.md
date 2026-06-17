# R1 Corpus Screen — 実 Suno で判明した「抽出器 BPM halving」と prior グリッド機構

`scripts/screen_corpus.py` で実生成音源を「指示値(プロンプト) vs 検出値」に突き合わせ、
さらに対照実験(A/B/C)と高 prior 診断で *崩壊の真因* を切り分けた記録。
データ: `examples/roundtrip/screen_2026-06-16.yaml`。

## 計器

各曲を `extract_physical_from_file` で抽出し、生成プロンプトの指示 bpm/key を
ground truth として保存性を分類（bpm: preserved/octave_half/octave_double/off、
key: preserved/parallel/relative/off）。音源バイナリは非同梱（licensing/サイズ）、
`audio_sha256` でバイトを固定。ファイル→曲は**特徴自己同定**で対応づけ、ファイル名に
依存しない（最初に破棄したテイクで踏んだ provenance 取り違えの再発防止）。

## 結果（最初の N=5、テンポ昇順）

| 曲 | 指示 bpm | 既定検出 | bpm | 指示 key | 検出 | key | リズム | high_ratio |
|---|---|---|---|---|---|---|---|---|
| 八百万の神話の世界 | 96 | 95.7 | ✅preserved | — | B minor | (指示なし) | acoustic 和楽器 | 0.0533 |
| 紫電の祈り | 168 | 172.3 | ✅preserved | D minor | D minor | ✅ | 和風ロック driving | 0.0615 |
| SO WHAT RUN | 172 | 117.5 | ❌off | — | F# minor | (指示なし) | jungle breakbeat | 0.0647 |
| (wafu×jungle) | 174 | 117.5 | ❌off | D minor | D minor | ✅ | jungle breakbeat | 0.0905 |
| アストラルトリガー | 175 | 117.5 | ❌off | F# major | F# major | ✅ | busy / double-time | 0.0692 |

当初は「高速 × breakbeat 構造が collapse を生む」「117.45 は Suno が遅く生成した結果」と
仮説したが、**下の対照実験と高 prior 診断で両方とも棄却された**。

## 対照実験 A/B/C（数字 vs 構造の分離）

key=D minor / 4/4 / 疎な電子パレット / テンポ変化なしを固定し、BPM とドラムだけ変えた
3 プロンプトを Suno に生成させスクリーン:

| 実験 | 指示 | 既定検出 | bpm | 指示 key | 検出 key |
|---|---|---|---|---|---|
| A 高速×単純(4つ打ち) | 176 | 89.1 | ❌octave_half | D minor | G major(off) |
| B 中速×breakbeat | 130 | 129.2 | ✅preserved | D minor | D minor |
| C 中速×単純(アンカー) | 130 | 89.1 | ❌off | D minor | D minor |

予測と逆に **breakbeat(B)が保存・単純拍(A,C)が崩壊** → **breakbeat 仮説は反証**。

## 高 prior 診断（崩壊＝抽出器か Suno か）

`librosa` tempo を `start_bpm=180` で再推定し、真テンポが音源に在るか確認:

| 曲 | 指示 | 既定 | start_bpm=180 | 結論 |
|---|---|---|---|---|
| 八百万 | 96 | 95.7 | 198.8 | 遅い曲は既定 prior で正解 |
| 紫電 | 168 | 172.3 | 172.3 | 保存 |
| SO WHAT | 172 | 117.5 | **172.3** | **抽出器 halving（真テンポ回復）** |
| wafu jungle | 174 | 117.5 | **172.3** | **抽出器 halving** |
| アストラル | 175 | 117.5 | **172.3** | **抽出器 halving** |
| A(176単純) | 176 | 89.1 | **172.3** | **抽出器 halving** |

高速 4 曲すべて start_bpm=180 で 172.3 を回復 → **真テンポは音源に在り、Suno は ~172 で
正しく生成**している。崩壊させたのは抽出器側。

> **計器化済み**: この手作業診断は `scripts/screen_corpus.py` に内蔵された。`compute_bpm`
> が `start_bpm` 引数を取り（既定 120.0＝librosa 既定で挙動不変）、screener が各曲を
> 既定 prior と高 prior(`HIGH_PRIOR_START_BPM=180`) で二択推定し、「既定で崩壊・高 prior で
> stated 回復」を `bpm_prior_recovery="recovered"` として分類、`bpm_halving_prior_recoverable`
> に集計する。これで BPM 非保存を *抽出器 halving* と *生成器不忠実* に自動弁別する。
> ※ 純合成インパルス列は prior 非感受（真テンポにロック）のため、回復の flip は実生成器の
> 曖昧な tempogram でのみ顕在化する。分類ロジック自体は注入値で単体テスト済。

## 確定した知見

### 1. 「崩壊」は抽出器 BPM halving であって Suno 不忠実ではない

高速トラックの低値（89.1 / 117.5）は、音源に存在する ~172 パルスを既定抽出器が降格させた
結果。`start_bpm=180` で全件 172.3 を回復することで実証。**Suno は明示 BPM をそれなりに
忠実に出す**（プロンプト学習にとって朗報）。

### 2. 「アトラクタ」の正体＝BPM グリッド × prior

全曲の onset-strength 自己相関ピークが同一集合 **[89.1, 117.5, 129.2, 172.3 …]**＝
`librosa` の hop/sr が決める離散グリッド。既定 prior（対数正規、中心 ~120）が
**「~120 に最も近いグリッド点」**を選ぶため、高速曲の真値(172 グリッド)が 117.5 に負ける。
89.1/117.45/136 は神秘的アトラクタでなく prior 偏向のグリッド選択。117.5 は 172 の
clean ÷2(=86) ですらない（172/117.5=1.47）。

### 3. breakbeat 構造は collapse の主因ではない

B(breakbeat 130) は保存し、A/C(単純拍) が崩壊。むしろ breakbeat の豊富な onset が
正しいグリッド点を支えた可能性。collapse はリズム genre でなく**抽出器の prior 偏向**で決まる。

### 4. R2-2a 半折り検出器の具体的バグ（actionable）

A(176→89.1, クリーン÷2, 2× エネルギーが音源に在る)で `is_ambiguous=False`、
`alt_strength_ratio=0.0`。原因は検出器が**ちょうど 2×(=178.2)の lag だけ**を見るのに、
グリッド量子化された実パルスは **172.3(=1.93×)** にあり lag bin が外れること。
さらに 117.5 系の collapse は 1.47× なので÷2モデルが原理的に非対応。
→ **修正方向**: 固定 2× lag でなく **1.8×–2.2× 近傍の支配ピーク探索**、または既定 tempo の
prior を上げる/適応化、もしくは onset-AC グリッド候補から octave 補正する。

### 5. key は概ね保存、brightness は編成順

key 保存は実曲で 6/7（off は A のみ＝疎な synth 和声で G major 誤検出、また転調曲は単一
グローバル key に潰れる設計限界）。high_ratio は acoustic(0.053) < wafu-rock(0.062) <
busy-electronic(0.069) < dense(0.091) と編成の明るさ/密度順に並びセンサー健全。

## 限界と次の一手

- **計測規律**: 保存率を測るとき、抽出器 halving と生成器不忠実を必ず分離する。
  `screen_corpus` に「高 prior 真テンポ回復チェック」を組み込めば保存率が正しく出る。
- **最優先の実装課題**: 既定 tempo prior の見直し（適応 prior or octave 補正）と R2-2a の
  近傍探索化。Design Memo 化して Codex 実装に渡す候補。
- calibratable 化（audio 同梱 + hash 一致）は licensing 確認後。現状 observation_log 相当。
