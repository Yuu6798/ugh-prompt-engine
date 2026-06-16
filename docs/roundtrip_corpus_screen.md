# R1 Corpus Screen — 実 Suno 5 曲の保存率と 117.45 BPM アトラクタ

`scripts/screen_corpus.py` で実生成音源を「指示値(プロンプト) vs 検出値」に突き合わせた
最初の base-rate スクリーン。データスナップショット: `examples/roundtrip/screen_2026-06-16.yaml`。

## 計器

各曲を `extract_physical_from_file` で抽出し、生成プロンプトの指示 bpm/key を
ground truth として保存性を分類する:

- **bpm**: `preserved`(±4%) / `octave_half`(÷2) / `octave_double`(×2) / `off`(非オクターブ誤検出)
- **key**: `preserved` / `parallel`(同根異旋) / `relative`(平行調) / `off`

音源バイナリは非同梱（licensing/サイズ）。`audio_sha256` で各テイクのバイトを固定し、
将来 audio を repo に入れれば calibratable へ自動昇格できる。ファイル→曲の対応は
**特徴自己同定**（検出 bpm/key がプロンプトと一致）で確定し、ファイル名には依存しない
（最初に破棄したテイクで踏んだ provenance 取り違えの再発防止）。

## 結果（N=5、テンポ昇順）

| 曲 | 指示 bpm | 検出 | bpm | 指示 key | 検出 | key | リズム | high_ratio |
|---|---|---|---|---|---|---|---|---|
| 八百万の神話の世界 | 96 | 95.7 | ✅preserved | — | B minor | (指示なし) | acoustic 和楽器 | 0.0533 |
| 紫電の祈り | 168 | 172.3 | ✅preserved | D minor | D minor | ✅preserved | 和風ロック driving | 0.0615 |
| SO WHAT RUN | 172 | **117.45** | ❌off | — | F# minor | (指示なし) | jungle breakbeat | 0.0647 |
| (wafu×jungle) | 174 | **117.45** | ❌off | D minor | D minor | ✅preserved | jungle breakbeat | 0.0905 |
| アストラルトリガー | 175 | **117.45** | ❌off | F# major | F# major | ✅preserved | busy hats / double-time | 0.0692 |

- **bpm 保存率 2/5 (0.4)** / **key 保存率 3/3 (1.0)**
- **bpm 誤検出 3 件すべて `octave_ambiguous=False`**（R2-2a 検出器の対象外）

## 知見

### 1. 117.45 BPM は実在のハードアトラクタ

**172 / 174 / 175 の 3 曲が、別曲なのに全て 117.45 ちょうど**に崩壊した。これは偶然でなく、
librosa のテンポ事前分布（対数正規、中心 ~120 BPM）が高速テンポを引き込む現象の着地点。
過去ログの「89.1 アトラクタ」「136」とあわせ、**単一値でなく「高速曲が中速帯(~89–136)へ
collapse する」現象で、117.45 が最頻アトラクタ**、と更新される。崩壊比は 0.67–0.68 で
÷2(0.5) でないため `octave_half` でなく `off`。

### 2. R2-2a 半折り検出器は構造的に盲

3 件の崩壊は ÷2 でない非オクターブ誤差なので、×2 自己相関比に基づく R2-2a 検出器は
原理的に発火しない（`bpm_octave_ambiguous=False`）。**fast→117 collapse を捕まえるには、
÷2 検出でなく「prior アトラクタ近傍 flag」**（例: 検出値が 110–125 帯に張り付き、かつ
onset 密度が高い場合に低信頼を付す）が必要、という設計示唆。

### 3. 崩壊の主因は「数字」でなく「リズム構造」の線が濃厚

保存した 2 曲（96 acoustic / 168 wafu-rock driving）は **非ブレイクビート**。崩壊した
3 曲は **すべて jungle/breakbeat ないし busy-hats/double-time**。特に 174 の曲は
プロンプトで "no tempo changes, consistent fast jungle groove" と明示＝**セクション転換が
原因でなく、ブレイクビートのグルーヴ自体が prior へ落とす**。ブレイクビートの強い
sub-beat 周期が推定器を混乱させる仮説を支持。残る交絡: 「高速かつ非ブレイクビート(>170 の
4 つ打ち)」と「低速ブレイクビート(120–140)」の 2 種が未取得で、数字 vs 構造の完全分離は未了。

### 4. key は保存され、brightness センサーは編成順に素直

key は実 3 曲すべて保存（D minor / D minor / F# major）。過去テイクで観測した E→D 非保存は
**破棄した provenance 曖昧テイクの不忠実**だった可能性が高く、1 サンプルでの早合点を回避できた。
high_ratio は acoustic(0.053) < wafu-rock(0.062) < busy-electronic(0.069) < dense-mix(0.091) と
おおむね編成の明るさ/密度順に並び、センサーの健全性を再確認。

## 限界と次の一手

- **N=5、うち高速帯に偏り**。base rate は暫定。中速ブレイクビート / 高速 4 つ打ちを足して
  「数字 vs 構造」を分離するのが最優先（知見 3 の確証/反証）。
- **転調曲（紫電 D minor→D major, アストラル final key lift）は単一グローバル key に潰れる**：
  検出器は曲内転調を表現できない（設計限界、バグでない）。
- calibratable 化（audio 同梱 + hash 一致）は licensing 確認後。現状は observation_log 相当
  （hash 固定済み）。
