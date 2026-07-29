# 設計書 — M2（抽出精度の検証: RPA/RCA・生き残り経路の校正）

**宛先:** Claude Code(実装) + slow-lane ランナー
**発行元:** Cowork（設計・検収）
**前提コミット:** `f1adfd2`（main、M1-real 記録マージ済み）
**設計入力（M1-real 本Go・2026-07 実測）:** 生き残り経路 = **`demucs_vocals_then_crepe`**。positive 4/4 sufficient（voiced_coverage 0.64–0.77・confidence 0.72–0.76）、negative 偽陽性ゼロ。melodia は #222 値域疑義で未測定（正規化裁定後に別途 dated 再入・本設計の対象外だが §1 の通り再入口は用意する）。
**スコープ:** 抽出精度の検証のみ。**旋律同士の比較（M3）・Recast 配線（M4）はやらない。**

---

## 0. なぜ M2 か（1分）

M1-real が言えたのは「実素材から**旋律らしき系列が十分な量・信頼度で取れる**」まで。
M2 の問いは「**取れた系列は正しいか**」。観測可能性と正確性は別物である——高い confidence で
自信満々に間違う抽出器はありうる（オクターブ誤り、伴奏漏れの追跡、ビブラートの平滑化失敗）。

M2 の真の成果物は合否だけではなく、生き残り経路の**誤差モデル**である:
「この経路は中央値 ±X cent の揺れ、Y% のオクターブ誤り、Z% の voicing 誤検出を持つ」。
M3 の比較器はこの誤差を**許容するように**設計されなければならない（誤差モデルなしに
類似度閾値を決めると、抽出誤差と編曲差分を区別できない）。M2 は M3 の目盛りの根拠を作る。

**規律の根（M0 から不変）:** 正解を持たない素材（自作 Suno 曲）で精度を主張しない。
精度は正解つき素材でのみ測る。閾値は実測前に凍結。一方向規則。

---

## 1. 現行資産（そのまま使う）

| 資産 | 用途 |
|---|---|
| `melody/extractors.py`（crepe 経路・重み/コード pin 済み） | 被校正対象。**M2 でコード変更しない** |
| `scripts/run_melody_observability.py` の provenance 機構 | 同じ pin 規律（weights/code/audio/registry hash）を精度 run にも適用 |
| `tests/fixtures/melody_bench/synthesis_specs.yaml` + builder | カテゴリ S（正解つき合成）の生成源。**spec が正解そのもの** |
| shifts=0 決定論化（PR #221） | 分離込み経路の再現性。n≥2 は一致確認として機能 |
| melodia adapter | 対象外。ただしハーネスは**抽出器非依存**に作り、#222 裁定後に melodia が同じ M2 を追走できる形にする |

新規モジュールは `melody/accuracy.py`（指標算出）と `scripts/run_melody_accuracy.py`（ハーネス）の2点に限定。

---

## 2. 指標（業界標準に合わせ、自作しない）

**`mir_eval.melody`（MIT ライセンス・純 Python・バージョン pin）を採用。** 自作指標は作らない
（比較可能性と実装検証コストのため。mir_eval は MIREX 系研究の標準実装）。

**スコアラー pin の閉包（M2b 前提整備）**: `mir_eval.melody.evaluate` / `to_cent_voicing`
は内部で `scipy.interpolate` と numpy を直接 import して実行するため、
`run_melody_accuracy._scorer_pins()` は mir_eval だけでなく scipy / numpy の
version + code sha256 も記録する（`_SCORER_RUNTIME_PACKAGES`）。librosa 系 backend
（抽出器オーケストレーション側の閉包）はスコアラー経路に無いため対象外。

| 指標 | 意味 |
|---|---|
| RPA (Raw Pitch Accuracy) | 有声フレームのうちピッチが ±50 cent 以内で当たった割合 |
| RCA (Raw Chroma Accuracy) | 同上・オクターブ差を無視 |
| **RCA − RPA** | **オクターブ誤り率の代理**（M3 のオクターブ正規化設計の直接入力） |
| VR / VFA | voicing 再現率 / 誤警報率（無声区間を旋律と誤認する率） |
| OA (Overall Accuracy) | 総合（参考記録。単一スコアなので判定には使わない — 総合スコア恒久禁止の精神） |
| 中央値絶対 cent 誤差 | mir_eval 外で追加算出（誤差モデルの中心値。M3 の許容幅の根拠） |

正解形式: 10ms hop の f0 系列（Hz、無声=0）。合成素材は spec から決定論導出、外部データセットは配布注釈をこの形式へ決定論変換（変換スクリプトも provenance pin）。

---

## 3. 素材 — 正解の出所で3カテゴリ（混同禁止）

### カテゴリ S: 正解つき決定論合成（CI 安全・commit 可）
spec → builder で合成した旋律+伴奏。正解 = spec そのもの。
- **S-direct**: 旋律トラック単体 → crepe 直（分離なし）。**抽出器そのものの上限測定**
- **S-fullstack**: 旋律+伴奏をミックス → demucs → crepe。**注意: Demucs は実音楽で訓練されており、合成音色の「vocals」判定は分布外**。この帯は**診断記録のみ**とし、合否バーを置かない（低くても crepe の欠陥と混同しない）

### カテゴリ V: 実歌声・正解注釈つき公開データセット（slow-lane・非 commit）
ライセンス確認を**取得前の関門**とする。候補（ライセンスは slow-lane 時に実確認して pin）:
| 候補 | 想定内容 | ライセンス見込み | 測る帯 |
|---|---|---|---|
| vocadito | 単独歌唱 ~40 クリップ + f0/note 注釈 | CC BY 4.0（要実確認） | **V-direct**: 実声で crepe 直 |
| MedleyDB (melody subset) | 実ミックス + melody f0 注釈 | 研究用・NC 系（要実確認・再配布不可） | **V-fullstack**: 分離込み実運用帯 |
| MIR-1K / ADC2004 等 | 補欠 | 条件不明瞭なら見送る | — |

条件を満たすデータセットが確保できない帯は**正直に「未測定」と記録**する（無理に代替素材で主張しない）。波形・注釈はリポジトリに commit しない（M1-real と同じ external 方式 + audio sha pin）。

### カテゴリ X: 自作 Suno 曲（正解なし）
**M2 では使用禁止**（精度を主張できない）。ただし V で校正済みの誤差モデルを X に外挿して
「観測値の信頼区間」を語ることは M3 以降で許す（測定と外挿を明示的に区別して）。

---

## 4. 事前登録バー（実測前に凍結・一方向）

registry に `m2_accuracy_bars`（registered_utc つき）を新設。**数値の根拠は公開文献の
典型帯（CREPE 原論文のクリーン単旋律 RPA ~0.90+、MIREX 系ボーカル抽出 0.75–0.85）から
実測前に設定**する。実データを見てから動かさない。

```yaml
m2_accuracy_bars:
  registered_utc: <PR時点>
  tolerance_cents: 50            # mir_eval 標準
  S_direct:                      # 抽出器の健全性バー（落ちたら経路自体を疑う）
    min_rpa: 0.90
    max_vfa: 0.15
  V_direct:                      # 実声・分離なし
    min_rpa: 0.80
    max_octave_gap: 0.05         # RCA - RPA
  V_fullstack:                   # 実運用帯（分離込み)・「校正済み」昇格バー
    min_rpa: 0.65
    max_octave_gap: 0.10
    max_vfa: 0.25
  S_fullstack: {}                # バーなし・診断記録のみ（Demucs 分布外のため）
  repeats_min: 2                 # 決定論確認（shifts=0 後は bit 一致するはず）
  one_way_rule: "バーを実測後に緩めない。落ちた帯の再挑戦は経路/実装の変更 + dated 再実測のみ"
```

**昇格の意味論:** V_fullstack を通過して初めて `demucs_vocals_then_crepe` は
「**calibrated**（誤差モデルつきで信頼できる）」となり、M3 設計が解禁される。

---

## 5. 実行プロトコル

1. **M2a（実装・CI 安全）**: `melody/accuracy.py`（mir_eval ラッパ + cent 誤差分布）、
   `run_melody_accuracy.py`（run/evaluate 二相・provenance pin は observability 版と同型）、
   registry へバー凍結、カテゴリ S fixture 追加。CI は S-direct を mock なし軽量実行
   （crepe が CI 不可なら fixture 済み結果 + ハーネス単体テスト。実測は slow-lane）。
2. **M2b（slow-lane・S 帯実測）**: S-direct 合否 + S-fullstack 診断記録。n≥2。
3. **M2c（slow-lane・V 帯実測）**: ライセンス実確認 → 取得 → external manifest（audio +
   **注釈ファイルの sha256 も** pin）→ V-direct / V-fullstack 実測 → evaluate。
4. **M2d（判定記録）**: dated 判定 + **誤差モデル 1 ページ**（cent 分布・オクターブ率・
   voicing 誤り・素材別ばらつき）を docs へ。これが M3 設計書の第1入力になる。

環境: slow-lane は M1-real と同じ Claude Code 環境を想定。データセット取得先
（zenodo 等）への到達性は §2 前提条件として最初に確認し、不達なら停止・報告
（M1-real の関所方式と同じ。ミラー探索禁止）。

---

## 6. Scorer pin の脅威モデルと境界

scorer pin（mir_eval/numpy/scipy 等の実行閉包の tamper-evidence）が**守る**のは、
受動的な取り違え・環境ドリフト・偶発的差し替え——別バージョンの数値ライブラリ、
wheel 外 BLAS、事前ロード、ビルド差、非決定的構成など「測定者が意図せず異なる
実装で測ってしまう」事故。これらは version/code/native/dist hash + 実行時検証
（DT_NEEDED 閉包・pre-bind/maps 検査・audit hook・mid-run 再検証）で fail-closed
検出される。

**守らない（脅威モデル境界）**: 測定プロセスの env/PATH/site-packages/ファイル
システムを**能動的に制御できる攻撃者**。この能力を持つ攻撃者は scorer 実装を
差し替えるより前に、より直接的に測定結果そのもの（report JSON・verdict）を偽造
できる。scorer pin をこの攻撃者に完全防御しても、同能力の攻撃者が迂回する下流
経路（結果ファイルの直接書き換え）が常に残り、防御の費用対効果が釣り合わない。
したがって scorer pin は「能動的攻撃者への完全な tamper-proofing」ではなく
「**受動的ドリフトの tamper-evidence**」と位置づける。

境界の含意:
- subprocess 硬化（ldconfig/git の絶対パス化・env allowlist）・native 閉包の
  DT_NEEDED 検証などは、受動的ドリフトの検出精度を高める範囲で実装する
  （実装済み・本 PR）。
- 「能動的 env 制御攻撃者だけが到達できる残余経路」（稀な env 組み合わせ、
  ローダ内部状態の TOCTOU、mmap 済みバイトの in-memory 改変など）は本境界内として
  acknowledged-boundary に分類し、際限ない実装細部の追跡はしない。
- 測定の真正性の最終担保は、この tamper-evidence に加えて「信頼された環境で
  測定を実行する」運用規律（M1-real/M2 の slow-lane）に依存する。

---

## 7. 判定分岐

- **calibrated（V_fullstack 通過)**: 誤差モデルを持って M3（正規化・対応・多軸比較）設計へ。
- **部分成立**（V-direct は通るが V-fullstack が落ちる）: 「抽出器は健全・分離が汚す」の帯地図。
  M3 は vocals stem 入力限定で先行しつつ、分離品質改善を別トラック化。
- **S-direct 不通過**: 経路自体の信頼を撤回する強い負の結果。crepe 経路を calibrated 候補から
  外し（一方向）、代替抽出器（melodia 裁定後・将来のライセンス適合 SOTA）の M1 再入を待つ。
- いずれでも: 数値の**外挿**（正解なし素材への適用）は M3 で誤差モデル明示つきでのみ許可。

---

## 8. PR 分割

| PR | 内容 | 受け入れ条件 |
|---|---|---|
| M2a | accuracy.py + ハーネス + バー凍結 + S fixture | mir_eval 一致テスト（既知入力で手計算値と一致）。バーが単一値で凍結。CI green（重依存なし） |
| M2b | S 帯実測記録 | S-direct 合否 + S-fullstack 診断が dated JSON + pin 完備 |
| M2c | V 帯実測記録 | ライセンス記録（原文引用+URL+日付）→ 実測 JSON。データセット非 commit |
| M2d | 判定 + 誤差モデル doc | 分岐（§7)の明記。M3 への入力として cent/octave/voicing の実数値 |

## 9. やってはいけないこと

- 正解なし素材（自作 Suno 曲）で RPA/RCA を算出・主張する。
- バー（§4)を実測後に緩める。S_fullstack の低値を理由に crepe を責める（分布外帯）。
- mir_eval を再実装・改変する。総合 OA 単独で合否を語る。
- ライセンス未確認データセットの取得・使用（確認は取得**前**）。波形/注釈の commit。
- M2 の中で類似度比較（M3）や Recast 配線（M4）に踏み込む。
- melodia を #222 裁定前に混ぜる。

一文: **M1 は「聞こえるか」を、M2 は「聞き間違えていないか」を問う。合否より大事な成果物は誤差モデルであり、それが M3 の比較器の目盛りになる。**
