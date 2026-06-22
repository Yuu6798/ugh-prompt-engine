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

## 低 prior 診断（÷2 は extractor で補正しない）

反対方向、つまり **reported-too-fast / doubling**（例: stated 60 が既定 prior で 117.45
に上がる）は `scripts/screen_corpus.py` の screener 診断に限定して扱う。`LOW_PRIOR_START_BPM=50`
で再推定し、「既定で非保存・低 prior で stated 回復」を
`bpm_doubling_prior_recovery="recovered"`、summary では
`bpm_doubling_prior_recoverable` として分離する。

この経路は **stated 真値を持つ screener でのみ成立**する。低 prior 単体を extractor に
載せると、正しく推定できている音源まで低速側へ引きずるため自己検証できない。実測では
sample fixture の `synth_01_slow_pad_c_major`（stated 60）は default 117.45 → low prior
60.09 へ回復する一方、`synth_03_mid_groove_g_major`（stated 120）は default 123.05 で
既に保存されているのに low prior で 60.09 へ崩壊する。screener は `prior_recovery` の
「default が preserved なら n/a」ガードにより後者を補正対象にしないが、extractor には
その stated 比較が無い。

検討した extractor 側 ÷2 補正は、以下の負の結果により採用しない。

1. **AC 振幅では分離不能**: 周期 T を持つ信号は lag 2T にも必ずピークを持つ。reported
   tempo が速すぎるのか、単に小節/拍の上位周期を含む正しい音楽なのかを、自己相関振幅だけ
   では決められない。
2. **beat-phase 交替は反証された**: pad の正検出候補 `synth_01` は weak/strong≈0.82 で
   交替が最弱に近い。逆に正検出扱いにしたい `synth_02` / `synth_05` の方が強い交替を示し、
   「交替が強いなら ÷2 補正」という規則は誤検出側へ倒れる。
3. **単独低 prior は自己検証不能**: 低 prior は `synth_01` の stated 60 を回復するが、
   stated 120 の `synth_03` も 60 へ崩壊させる。stated 真値との比較なしに「回復」か
   「破壊」かを判定できない。

したがって R2-2e の境界は明確に **screener 限定**とする。`PhysicalRPE` / extractor の
`bpm_octave_ambiguous` と `bpm_candidates` は faster-side（reported-too-slow）専用のまま
維持し、÷2 自動補正や新フィールドは追加しない。

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
  `screen_corpus` の高 prior / 低 prior 回復チェックで、抽出器 halving / doubling と
  生成器不忠実を分けてから保存率を読む。
- **最優先の実装課題**: 既定 tempo prior の見直し（適応 prior or octave 補正）と R2-2a の
  近傍探索化。Design Memo 化して Codex 実装に渡す候補。
- calibratable 化（audio 同梱 + hash 一致）は licensing 確認後。現状 observation_log 相当。

## Drive-backed materialization (R1-CORPUS-DRIVE)

R1 corpus の実音源は repo に入れない。`examples/roundtrip/screen_2026-06-16.yaml`
は `audio_sha256` を content pin とし、確認済みの一部 song だけに `drive_file_id`
を取得ポインタとして持つ。`drive_file_id` は provenance metadata であり、loader の
採用判定には使わない。ローカルにダウンロード済み、アップロード済み、または別経路で
受け取ったファイルを `--source-dir` に置き、sha256 が pin と一致したものだけを
gitignore cache に materialize する。

```bash
python scripts/fetch_corpus.py examples/roundtrip/screen_2026-06-16.yaml \
  --source-dir /path/to/downloaded-audio \
  --out examples/roundtrip/cache/resolved_screen.yaml
python scripts/screen_corpus.py examples/roundtrip/cache/resolved_screen.yaml
```

`scripts/fetch_corpus.py` は `--source-dir` を一度走査して sha256 を計算し、一致した
source を `examples/roundtrip/cache/<id>.<ext>` へコピーする。出力 YAML の `songs` は
`screen_corpus.py` がそのまま読める `audio` / `audio_sha256` / `bpm` / `key` /
`time_signature` 形式へ正規化される。不一致の locator がある場合は
`status.reason=sha256_mismatch`、該当 sha256 が見つからない場合は `status.reason=not_found`
として記録し、未解決 song は `songs` から除外する。これにより CI や reviewer 環境で
private 音源が無くても loader は graceful に空または部分解決の YAML を出力できる。

Private Google Drive の共有状態、ファイル名、取得方法は再現性の根拠にしない。再現性の
根拠は `audio_sha256` と materialized bytes の一致であり、`drive_file_id` 欠落の
upload-only take も同じ source-dir 解決で扱う。`examples/roundtrip/cache/` は gitignore
対象で、licensing 未確定の実音源バイトを commit しないための境界である。

### CV-scale 実音源校正（2026-06-22, R2-2f）

上記 loader で実音源 7 本（`wafu_jungle_174` のバイトは未取得で `not_found`）を
materialize し、`compute_bpm` の confidence と含意 CV（`= (1 - confidence) / CV_SCALE`）を
実測した。`BPM_CONFIDENCE_CV_SCALE` は合成音の CV∈[0.024, 0.035] から暫定設定された
`5.0` を、実音源で初めて検証する目的。

| id | det bpm | 真値±5 | confidence | implied CV |
|---|---|---|---|---|
| shiden_no_inori | 172.27 | ✅ preserved | 0.901 | 0.0198 |
| expB_mid130_breakbeat | 129.20 | ✅ preserved | 0.871 | 0.0258 |
| yaoyorozu_shinwa | 95.70 | ✅ preserved | 0.831 | 0.0339 |
| expA_fast176_simple | 89.10 | ❌ octave_half(true 176) | 0.852 | 0.0296 |
| astral_trigger | 117.45 | ❌ off(true 175) | 0.813 | 0.0375 |
| expC_mid130_simple_anchor | 89.10 | ❌ off(true 130) | 0.800 | 0.0400 |
| so_what_run | 117.45 | ❌ off(true 172) | 0.798 | 0.0404 |

**結論（R2-2f closeout）**:

1. **`CV_SCALE=5.0` は実音源で妥当 → 据え置き確定**。真値±5BPM 内の preserved 3 本は
   confidence 0.83–0.90 で Q1-3 契約（>0.7）を満たし、契約を**実音源で初実証**した
   （従来は合成のみ）。実 CV∈[0.020, 0.040] は合成想定 [0.024, 0.035] よりやや広いが
   契約は割れない。production コード変更なし。
2. **CV-confidence は regularity-only で誤 BPM を検出しない**。BPM が誤った 4 本
   （octave_half 1 = expA / off 3 = astral・so_what・expC）も confidence 0.80–0.85 と高い
   （誤検出した拍グリッドも規則的なため）。
   これは R2 closeout の「bpm を R3 信頼ノブから除外」判断を**実データで再確証**する。
   正しさの surfacing は `bpm_octave_ambiguous` フラグ + prior 回復診断の役割であり、
   CV-scale の調整事項ではない。

決定論注記: 各 det bpm / key / high_ratio は `screen_2026-06-16.yaml` の `measured`
記録と完全一致し、materialize したバイトが screened バイト本体であること（sha256
content-address の正しさ）と抽出器の環境非依存な決定性を裏づけた。

再現性注記（重要）: `fetch_corpus.py` は **Drive を叩かず** `--source-dir` 内のバイトだけを
sha256 照合する。よって素の checkout / CI（手動 DL 無し）では **materialize 対象 7 本すべて
`not_found`**（loader が Drive を自動取得することはない）。ただし **2026-06-22 に upload-only
だった 4 本（astral_trigger / expA / expB / expC）を Drive へアップロードし `drive_file_id` を
付与済み**（byte-size 一致で provenance 確認、manifest 反映）。これで screen 対象 7 本すべてが
`drive_file_id`（在処ポインタ）を持ち、Drive アクセスを持つ人が手動 DL して `--source-dir` に
置けば **フル 7 本 screen を再現できる**。残る未解決は screen 対象外の `wafu_jungle_174`
（バイト未取得・`drive_file_id` 無し・`not_found`）のみで、これが R1 の最後の artifact 作業。
**CV-scale 結論（5.0 確定）** は preserved 2 本（shiden 0.901 / yaoyorozu 0.831）が
`±5BPM で conf>0.7` 契約を満たし、so_what（`bpm_relation: off`・117.45/172、conf 0.798）が
「**誤 BPM でも beat が規則的なら高 conf**」＝CV は regularity-only を示すことで成立する。
so_what は非octave の off であって halving（octave_half）ではない。halving 固有の例（expA
octave_half 89/176）も 2026-06-22 アップロードで `drive_file_id` 付きとなり、Drive アクセス下で
取得できる（従来の upload-only 制約は解消）。
