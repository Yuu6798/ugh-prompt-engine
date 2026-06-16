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
**プロンプト文字列と計測値（この時の記録値・音源無しでは再測不可）を本書に保存**する。
計測値は再実行可能な証拠ではなく観測ログであり、Q1-3/K2 が再利用するには音源 +
manifest の保存が前段で要る（§4・§6）。

---

## 1. 計測サマリー（全 4 曲）

| 曲 | 真の意図（プロンプト） | bpm(raw→score) | key | 拍子 | brightness(Hz→label) | active | valley | stereo |
|---|---|---|---|---|---|---|---|---|
| **C-orig** Celtic RPG（原曲アップロード） | 86 BPM / D Dorian / 6/8 | 83.35→83 | A# major | 4/4 | 1235→(neutral) | 0.972 | 0.142 | 0.551 |
| **C-gen** Celtic（私の編曲プロンプト生成） | 83 / A# major / bright / wide | 89.10→89 | A# major | 4/4 | 1430→(neutral) | 0.993 | 0.087 | 0.422 |
| **J-rock** J-Pop rock（ユーザー原曲プロンプト生成） | 175 / E Major / 4/4 / bright | 89.10→89 | E major | 4/4 | 3696→bright | 0.992 | 0.034 | 0.398 |
| **J-ebm** EBM（J-rock の物理固定・意味層を EBM に差替生成） | 175 / E major / bright（意味=club EBM） | 89.10→89 | E major | 4/4 | 3284→bright | 0.998 | 0.045 | 0.243 |

### プロンプト全文（再現用）

省略なしの逐語コピー。音源は未コミットなので、再生成にはこの全文を用いる。

- **C-orig 真値**: "Mystical Celtic RPG town theme, 86 BPM, D Dorian, 6/8. Low whistle,
  Celtic harp, uilleann pipes, soft fiddle, bodhrán, deep drone, airy choir, distant
  bells, forest ambience. Ancient hidden town at twilight, foggy, sacred, magical,
  nostalgic, slightly melancholic. Gentle vocal melody, quiet verses, floating chorus.
  No cheerful tavern music, no bright pop, no EDM, no rock drums."
- **C-gen（compose 出力）**: "Bright, Celtic folk arrangement with airy pads and a gentle
  lilt atmosphere. wistful and pastoral / ancient, misty highland atmosphere track.
  83 BPM. A# major. Intro: sparse, airy, single melodic line; role=lone tin whistle sets
  the mood. Verse: add fiddle, low density; role=introduce fiddle motif. Bridge:
  arpeggiated harp, mid density; role=harp transition. Verse2: fiddle plus bodhran pulse;
  role=develop the melody. Chorus: whistle, fiddle, harp, bodhran together; bright and
  wide; role=full ensemble peak. Verse3: ornamented fiddle, rolling rhythm; role=variation
  with ornamentation. Chorus: full ensemble, brightest moment; role=final lift."
- **J-rock 真値**: "J-Pop with high-energy rock and electronic elements. Bright, distorted
  electric guitars play syncopated power chords and melodic lead lines. A driving drum kit
  features a prominent snare on 2 and 4 with rapid 16th-note hi-hat patterns and frequent
  crash cymbal accents. A thick, distorted synth bass follows the kick drum's rhythmic
  patterns. Shimmering digital pads and staccato synth arpeggios provide harmonic texture.
  The arrangement features a high-tenor male vocal with occasional double-tracking and
  light reverb. The tempo is 175 BPM in the key of E Major. The bridge features a half-time
  feel with prominent acoustic guitar strumming before returning to the original tempo for
  the final chorus. The track ends with a clean electric guitar melody over a sustained
  synth pad."
- **J-ebm（compose 出力）**: "Bright, club-ready EBM / electronic body music for a dark
  warehouse floor atmosphere. pulsing, hypnotic, relentless / industrial, sweaty,
  machine-driven track. 175 BPM. E major. Intro: filtered, rising energy; role=low-density
  build. Verse: mid density, driving pulse; role=groove establishes. Chorus: full density,
  brightest, widest; role=peak energy. Bridge: strip back, then rebuild; role=tension
  break. Chorus: full density, relentless; role=final peak. Outro: fade density, sustained
  texture; role=wind down. Avoid: organic acoustic instruments; soft ballad sections;
  clean pop vocals."

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

**統制**: 物理層の楽譜を固定（175 BPM / E major / bright）、
**意味層だけ rock → club EBM に差し替え**。

> **重要な前提 — どの欄が実際に Suno に渡ったか**: `compose`（`prompt_renderer._segments_for`）が
> **locked PhysicalLayer の数値ノブ**として出すのは **bpm / key / brightness のみ**。
> `time_signature` は renderer に segment が無く **完全に未送出**。
> `stereo_width` / `active_rate_target` / `valley_depth_target` の**数値**は最低優先の
> `physical.optional` 行にあり、J-ebm プロンプトは長く `prompt_max_chars=650` で
> truncate されたため **スキーマ数値としては未送出**。
> **ただし構造プロンプト（各 section の physical 文）には density / width の質的キュー
> （`low-density` / `mid density` / `full density` / `widest` 等）が含まれ Suno に届いている**。
> よって数値ノブとして固定・送出されたのは bpm / key / brightness のみ。
> active / valley / stereo は数値ノブとしては未統制だが、質的キューは皆無でない
> （両プロンプトで文言が異なり厳密な A/B 統制変数にはならない）。

| 計測欄 | Suno への送出形態 | J-rock | J-ebm | 読み |
|---|---|---|---|---|
| bpm | ✅ 数値ノブ (175) | 89 | 89 | **判定不能** — 両値とも §4 の 89.1 アトラクタの可能性。Suno が 175 を生成した証拠が無く「再現」とは言えない |
| key | ✅ 数値ノブ (E major) | E major | E major | 一致＝送出ノブが再現 |
| brightness | ✅ 数値ノブ (bright) | 3696 (bright) | 3284 (bright) | 同帯域＝送出ノブが再現 |
| 拍子 | ❌ 完全未送出（segment 無し） | 4/4 | 4/4 | **制御の証拠でない**（既定/スタイル） |
| active | 🔸 数値未送出／構造に density 語 | 0.992 | 0.998 | 数値ノブとして未統制（質的キューは両者で異なる） |
| valley | 🔸 数値未送出／構造に density 語 | 0.034 | 0.045 | 数値ノブとして未統制（質的キューは両者で異なる） |
| stereo | 🔸 数値未送出／J-ebm 構造に `widest` | 0.398 | 0.243 | 数値ノブとして未統制・かつ未校正 |

**耳判定（ユーザー）**: J-ebm は J-rock と**「全く別物の EBM」**になった。

### 結論

1. **送出した物理ノブのうち key / brightness は再現した** — 意味層を全面差替しても
   この 2 欄は一致。= プロンプトに渡した物理指示を演奏者が保った。**双方向再現性は
   key / brightness で成立**。**bpm は送出したが判定不能** — 両値 89 は §4 の
   89.1 アトラクタと交絡し、175 が保たれた証拠にならない。**time_signature は完全に
   未送出。active / valley / stereo は数値ノブとしては未送出**（構造プロンプトに
   density/width の質的キューは届くが両者で文言が異なり統制変数にならない）。
   これらを厳密な制御変数にするには renderer への数値ノブ追加か prompt 予算の調整が、
   bpm の判定には音源/真値の保存が要る。
2. **物理層と意味層は直交** — 意味層を全面差替しても、送出した物理欄は不動。層設計が機能。
3. **意味層は効いた（耳判定）が、計器は盲目** — rock/EBM の差は音色/リズム事象の
   次元にあり、現状センサーに欄が無い。**意味層 grip を機械確認できる日 = T3**
   （事象レベル欄＝`score_centric_planning.md` §6 急所1）の動機が実証された。

**総合判定**: 双方向性テストは、**送出かつ計器が信頼できる物理ノブ（key / brightness）
について成功**（ユーザー合意）。bpm は送出したが計器のアトラクタで判定不能、
数値ノブ未送出の欄（meter / density / stereo；質的キューは構造プロンプトに在るが
統制変数でない）は本 A/B の検証対象外。
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
| **Q1-3** | BPM 89.1 アトラクタ + 175→89 半折りの**観測**（再測不可）。問題の存在を示す動機づけであって校正データではない（後述） |
| **T2** | 送出かつ計器が信頼できる数値ノブ（**key / brightness のみ**）で往復一致を確認。bpm は判定不能、meter/active/valley/stereo は数値ノブ未送出で検証対象外（§3）。本ケースは実生成器版の T2 先取りだが、検証済み欄を上記に限定して引き継ぐこと |
| **T3** | 意味層 grip は耳でしか判定できず → 音色/リズム事象センサーの動機 |
| **K2** | 物理固定・意味差替で「別物」生成を確認 = 意味層 grip 有り（耳）。Suno 転移の最初の実点 |

**全体注意**: n=1（Suno は確率的）。効果量主張には少数バッチ反復が要る。
本書は「効きそう/効かなそう」の最初の実点として読む。

**再実行可能性の限界（重要）**: §0 のとおり音源は ephemeral で未コミットのため、
**本書の計測値は再測できない観測**である。Q1-3 の BPM 信頼度再設計は候補推定器を
音源に当て直して比較する作業なので、**本書の行から直接は校正できない**。
これらを校正データ化するには、音源 + 真値（プロンプト記載の BPM 等）+ manifest を
別途リポジトリに保存して再実行可能にする前段タスクが要る。それまで本書は
「問題が実在することの証拠」に留め、校正の入力とは扱わない。

---

## 7. R1 corpus manifest 化

R1 で §1 / §3 の 4 ケースを
[`examples/roundtrip/corpus/manifest.yaml`](../examples/roundtrip/corpus/manifest.yaml)
へ構造化した。音源が未コミットのため、これらは `audio_locator` / `audio_hash` を持たない
`observation_log` として保存し、バッチ実行では記録済み `measured` を転記する。
特に J-rock / J-ebm の 175→89 BPM 問題ケースは、問題の観測ログとして残すだけで、
R2 の alternate BPM estimator を再実行できる calibratable artifact ではない。R2-1 を
進めるには、人間トラックで保存済み音源 + SHA-256 hash を追加する必要がある。

同 manifest には、コミット済み CC0 synth 音源
`examples/sample_input/synth_05_fast_bright_d_major.wav` を backing にした
`calibratable` レコードも 1 本含めた。こちらは SHA-256 照合後に
audio → RPE → draft Score を再実行し、`send_form == "numeric_knob"` の intent 欄だけを
比較する。
R1 箱実装の locator は repo-relative local path のみを受け付ける。artifact URI
（`https://...` / `s3://...` 等）は resolver 実装まで manifest load 時に reject する。

**`send_form` 運用規約**（各 intent 欄が生成器へどう渡ったか）:
`numeric_knob` = スキーマ物理欄として明示レンダリング・送出された値（`prompt_renderer`
が locked PhysicalLayer を出す bpm / key / brightness 等）で**保存性比較の対象**。
`qualitative_cue` = 構造プロンプトの散文・質的キューとしてのみ届いた値（density / width
語等）で比較対象外。`not_sent` = プロンプトに一切現れない欄（renderer に segment 無し
等）で比較対象外。盲目センサーの一致を制御証拠にしない（§3）原則は `numeric_knob` 欄の
**一致**にのみ適用し、`numeric_knob` 欄の**不一致**（例: C-gen の bright→neutral）は
非保存として表に残す。

再生成コマンド:

```bash
svprpe roundtrip-corpus examples/roundtrip/corpus/manifest.yaml --format json
```

固定 snapshot:

- [`examples/roundtrip/corpus/batch_report.json`](../examples/roundtrip/corpus/batch_report.json)
