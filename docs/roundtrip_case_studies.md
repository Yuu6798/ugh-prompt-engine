# Suno 往復テストケース結果 — 双方向性 / 制御性の実生成器検証

**Status**: RESULTS LOG（個別テストケース結果。メモリ要約とは別管理）
**Created**: 2026-06-13
**Track**: 双方向再現性（[`score_centric_planning.md`](score_centric_planning.md) §2）/ 制御性（[`controllability_poc.md`](controllability_poc.md) K 系列）
**Generator**: Suno（外部・確率的）
**Pipeline**: `svprpe measure` / `svprpe transcribe`（本日マージの T0/T1, PR #70/#71）+ `svprpe compose`

> 本ドキュメントは実 Suno 生成曲を使った往復・制御性テストの**生データと知見**を、
> 個別ケースとして蓄積する場所。検証プロトコルの設計や校正タスクの仕様は
> 各トラックの doc（`score_centric_planning.md` / `controllability_poc.md` /
> `roadmap_goal1.md`）に置き、ここには「何を入れて何が出たか」を残す。

---

## 0. テスト全体の構図

```text
原曲プロンプト ──Suno──▶ 曲A ──measure/transcribe──▶ 採譜A
楽譜編集（意味層差替）──compose──▶ プロンプトB ──Suno──▶ 曲B ──measure──▶ 採譜B
                                                          ↑ A と B を比較 = 制御性 A/B
```

合計 4 曲（原曲アップロード 1 + 生成 3）を計測。音源は ephemeral のため未コミット、
**プロンプト文字列と計測値（再現可能な数値）を本書に保存**する。

---

## 1. 計測サマリー（全 4 曲）

| 曲 | 真の意図（プロンプト） | bpm(raw→score) | key | 拍子 | brightness(Hz→label) | active | valley | stereo |
|---|---|---|---|---|---|---|---|---|
| **C-orig** Celtic RPG（原曲アップロード） | 86 BPM / D Dorian / 6/8 | 83.35→83 | A# major | 4/4 | 1235→(neutral) | 0.972 | 0.142 | 0.551 |
| **C-gen** Celtic（私の編曲プロンプト生成） | 83 / A# major / bright / wide | 89.10→89 | A# major | 4/4 | 1430→(neutral) | 0.993 | 0.087 | 0.422 |
| **J-rock** J-Pop rock（ユーザー原曲プロンプト生成） | 175 / E Major / 4/4 / bright | 89.10→89 | E major | 4/4 | 3696→bright | 0.992 | 0.034 | 0.398 |
| **J-ebm** EBM（J-rock の物理固定・意味層を EBM に差替生成） | 175 / E major / bright（意味=club EBM） | 89.10→89 | E major | 4/4 | 3284→bright | 0.998 | 0.045 | 0.243 |

### プロンプト全文（再現用）

- **C-orig 真値**: "Mystical Celtic RPG town theme, 86 BPM, D Dorian, 6/8. Low whistle,
  Celtic harp, uilleann pipes, soft fiddle, bodhrán, deep drone, airy choir, distant
  bells, forest ambience. ... quiet verses, floating chorus. No cheerful tavern music,
  no bright pop, no EDM, no rock drums."
- **J-rock 真値**: "J-Pop with high-energy rock and electronic elements. Bright,
  distorted electric guitars ... snare on 2 and 4 with rapid 16th-note hi-hat ...
  175 BPM in the key of E Major. The bridge features a half-time feel ..."
- **J-ebm（compose 出力）**: "Bright, club-ready EBM / electronic body music for a dark
  warehouse floor atmosphere. pulsing, hypnotic, relentless / industrial, sweaty,
  machine-driven track. 175 BPM. E major. ... Avoid: organic acoustic instruments;
  soft ballad sections; clean pop vocals."

---

## 2. ケース 1 — 計器の有効帯域（Celtic vs J-Pop）

真値が分かっている 2 曲で、採譜の的中/失敗を対比。

| 欄 | Celtic（C-orig） | J-Pop（J-rock） |
|---|---|---|
| key | D Dorian → ❌ A# major（旋法を major/minor に丸め） | E Major → ✅ E major |
| 拍子 | 6/8 → ❌ 4/4（複合拍子を潰す） | 4/4 → ✅ 4/4 |
| brightness | foggy → 中間帯（言い切らず neutral） | bright → ✅ bright（初の bright 発火） |

**知見**: 計器は**西洋・長調/短調・4/4・明るい帯域では的中、旋法・複合拍子・
アコースティックでは盲目**。「正しさは本質でないが、有効帯域の中では針は当たる」
（`score_centric_planning.md` §1.1）が実曲で裏付けられた。
**注意**: 盲目のセンサーが返す「一致」（例: C 系列で A# major / 4/4）は
grip の証拠にならない（同じ誤読の可能性）。信頼できる一致は有効帯域内の J-Pop のみ。

---

## 3. ケース 2 — 双方向性 / 制御性 A/B（J-rock vs J-ebm）★本命

**統制**: 物理層の楽譜を固定（175 BPM / E major / 4/4 / bright / 高密度・同一構造）、
**意味層だけ rock → club EBM に差し替え**。

| 計測欄 | J-rock | J-ebm | 差 |
|---|---|---|---|
| bpm | 89 | 89 | 完全一致 |
| key | E major | E major | 完全一致 |
| 拍子 | 4/4 | 4/4 | 完全一致 |
| brightness | 3696 (bright) | 3284 (bright) | 同帯域 |
| active | 0.992 | 0.998 | ほぼ同 |
| valley | 0.034 | 0.045 | ほぼ同 |
| stereo | 0.398 | 0.243 | 共に narrow |

**耳判定（ユーザー）**: J-ebm は J-rock と**「全く別物の EBM」**になった。

### 結論

1. **物理層は効くし再現する** — 意味層を全面差替しても物理計測がほぼ完全一致。
   = 物理層の楽譜は演奏者が従う本物の指示。**双方向再現性が物理層で成立**。
2. **物理層と意味層は直交** — 意味層を変えても物理計測は不動。層設計が機能。
3. **意味層は効いた（耳判定）が、計器は盲目** — rock/EBM の差は音色/リズム事象の
   次元にあり、現状センサーに欄が無い。**意味層 grip を機械確認できる日 = T3**
   （事象レベル欄＝`score_centric_planning.md` §6 急所1）の動機が実証された。

**総合判定**: 双方向性テストは **BPM 計器の一点を留保すれば成功**（ユーザー合意）。
C4 決定論シンセでなく**実 Suno での往復**であり、T2 を越えて K2 入口に触れている。

---

## 4. 留保事項 — BPM 計器の 89.1 アトラクタ疑い

- 生成 3 曲（C-gen / J-rock / J-ebm）が**すべて raw 89.10 BPM ちょうど**。
  原曲アップロード C-orig は 83.35、合成曲は 117 なので「常時 89」ではないが、
  **異なる Suno 生成 3 曲が同一値**は偶然にしては不自然。
- J-rock は真値 175 → 89（ほぼ半分）。半テンポ曖昧性に加え、推定器が **89 付近の
  アトラクタに吸い寄せられている疑い**。
- これは**採譜（音→楽譜）の復路で BPM のみが誤差を注入する漏れ穴**であり、
  パイプラインのエラーではなく**計器（BPM 推定器）の校正課題**。
- → [`roadmap_goal1.md`](roadmap_goal1.md) **Q1-3（BPM 信頼度の再設計）を最優先**。
  再採譜を連鎖させると BPM だけがテンポ半分へドリフト伝播する点に注意。

---

## 5. その他の針の所見

- **stereo_width 未校正**: 全曲で "wide" 指定でも narrow 値。ツマミ死かセンサー
  不良か切り分け不能。T1 が `stereo_width` を TODO 扱いにしているのは妥当。
- **avoid / 除外指定**: J-ebm の "no acoustic / no clean pop vocals" は耳判定で
  効いた（EBM 化に成功）が、機械計測対象外。効くプロンプト表現カタログの候補。

---

## 6. ロードマップへの差し戻し

| 宛先 | 入力となる知見 |
|---|---|
| **Q1-3** | BPM 89.1 アトラクタ + 175→89 半折り。実曲 4 点を校正データに使える |
| **T2** | 物理層の往復一致を実証（BPM 除く）。本ケースは実生成器版の T2 先取り |
| **T3** | 意味層 grip は耳でしか判定できず → 音色/リズム事象センサーの動機 |
| **K2** | 物理固定・意味差替で「別物」生成を確認 = 意味層 grip 有り（耳）。Suno 転移の最初の実点 |

**全体注意**: n=1（Suno は確率的）。効果量主張には少数バッチ反復が要る。
本書は「効きそう/効かなそう」の最初の実点として読む。
