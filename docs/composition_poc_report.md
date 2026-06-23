# Composition PoC Report — C4 エンドツーエンドデモ

**Status**: DONE（決定論パスで実施。外部生成器での手動ループは未実施 — §6 参照）
**Date**: 2026-06-12
**Upstream**: [`composition_poc_planning.md`](composition_poc_planning.md) Phase C4 /
[`composition_score_product_brief.md`](composition_score_product_brief.md) PoC 5
**Relates to**: [`controllability_poc.md`](controllability_poc.md)（K 系列・grip）

---

## 1. 目的と検証した仮説

C4 のゴールは「1 曲分のフルループ（Score → prompt → 演奏 → audit）を回し、
PoC 1–5 が統合された状態で成立することを示す」こと。検証した仮説:

> Composition Score を不変に保ったまま演奏（生成）だけが変わったとき、
> audit 制御盤の針はその差を**連続量として**捉えられる。
> つまり「Score=作品本体、生成=演奏」の分離は観測可能である。

## 2. 手法 — 決定論的シンセ演奏者による代替

計画上の手順 3「Suno/Udio で音源生成（手動）」は外部サービス依存のため、
本リポジトリの原則（ローカル完結・同一入力 → 同一出力）に合わせて
**決定論的シンセ演奏者**（`scripts/compose_e2e_demo.py` の `perform()`）で代替した。

「AI は演奏者」（ブリーフ D5）の読み替えとして、演奏者を
**style パラメータ付きの合成器**としてモデル化し、同一 Score を 2 テイク演奏させた:

| テイク | スタイル | 意味 |
|---|---|---|
| `first_take` | tempo bias −28 / +4 半音転調 / bright voicing / ダイナミクス平坦化 | 指示を半分無視した演奏（外部生成器の典型的な逸脱の再現） |
| `faithful_take` | バイアスなし | Score に忠実な演奏 |

ループ全体:

```text
composition_score.yaml
  → svprpe compose 相当 (ExternalPromptAdapter)  → generated_prompt.txt
  → perform(score, style)                        → {style}.wav      （決定論的）
  → extract_rpe_from_file()                      → {style}_rpe.json
  → build_audit_report()                         → {style}_audit.{md,json}
  → 2 テイクの針位置比較                          → needle_comparison.md
```

成果物は `examples/composition/midnight_signal/e2e/` に格納。コミットするのは
audit レポート（`*_audit.{md,json}`）と針比較表（`needle_comparison.md`）のみで、
WAV / 抽出 RPE / プロンプトは決定論的に再生成可能な中間生成物として非コミット
（同ディレクトリの `.gitignore` 参照）。再現と検証:

```bash
python scripts/compose_e2e_demo.py            # 成果物の再生成
python scripts/compose_e2e_demo.py --verify   # コミット済み成果物との一致検証
pytest tests/test_composition_e2e.py -q       # 回帰テスト
```

演奏者の構造層解釈は Score 語彙のキーワードヒューリスティック
（"near silence" → drone 0.10 / "low density" → 0.25 / "sparse" → 0.45 /
"full energy" → 0.85 / "clear rests" → 各小節最終拍を無音化）。
PoC 範囲の簡易解釈であり、汎用 NLU ではない。

## 3. 結果 — 針の移動

（下表は実行時点のスナップショット。再生成される一次ソースは
`examples/composition/midnight_signal/e2e/needle_comparison.md`）

| knob | layer | target | first_take | faithful_take | dev (first) | dev (faithful) | 針の移動 |
|---|---|---|---|---|---:|---:|---|
| bpm | physical | 128 | 99.38 | 129.2 | −28.62 | 1.2 | → target |
| key | physical | C minor | E minor | C minor | 1 | 0 | → target |
| time_signature | physical | 4/4 | 4/4 | 4/4 | 0 | 0 | = flat |
| active_rate | physical | 0.90–0.93 | 1.0 | 0.9301 | 0.07 | 0.0001 | → target |
| valley_depth | physical | 0.15–0.25 | 0.0043 | 0.1593 | −0.1457 | 0 | → target |
| brightness | physical | dark | 0 | 0 | 0 | 0 | = flat |
| stereo_width | physical | wide | — | — | — | — | sensor missing |
| core | semantic | introspective night drive | (rule-based 記述) | (rule-based 記述) | 1 | 1 | = flat |
| grv | semantic | deep_house, ambient | mid-focused, dense | bass-heavy, dense | 1 | 0.5 | → target |
| delta_e | semantic | gradual build … | sustained_energy | gradual_build | 0.7 | 0.0544 | → target |

**6 本の針が target 方向へ移動**（bpm / key / active_rate / valley_depth / grv / delta_e）。
特に delta_e は、faithful_take の構造演奏（intro 0.25 → verse 0.45 → chorus 0.85）から
`gradual_build` がセンサー側で再検出された — **構造層の作曲意図が物理演奏を経由して
意味層の観測に戻ってきた**ことを意味し、三層往復の最初の観測になる。
ただし演奏者は決定論的な代替であるため、これは **PoC 5 の決定論パスでの実証**
であって、確率的な外部生成器に対する実証ではない（§6）。

## 4. 考察 — 動かなかった針はセンサーの発見である

audit は verdict を出さない計器なので、「動かなかった針」も等価に報告する:

- **brightness**: 帯境界が `semantic_rules.yaml` の bright ≥ 0.6（4kHz 以上のエネルギー
  比率）に由来し、dark 帯が極端に広い。bright voicing の first_take ですら 0 のまま。
  → **K1（grip 代表マップ）で brightness の帯再校正が必要**という具体的な根拠が取れた。
  **追記（2026-06-12）**: K1 での追試を経て正規センサーを `spectral_centroid` に
  再設計済み（[`controllability_poc.md`](controllability_poc.md) §5.1）。現行の
  針比較表（`needle_comparison.md`）は centroid 観測値を表示する。
- **core**: `por_lexical_similarity` は作曲者の詩的言語（"introspective night drive"）と
  ルール由来の音響記述の間で常に 0。意味層の語彙ギャップそのものであり、
  センサーの感度限界として記録する（合否ではない）。
- **stereo_width**: 演奏者が mono のため `sensor missing`。針の欠測が正しく欠測として
  表示される（0 や false にならない）ことの確認になった。

副次的発見: verse の "clear rests" を休符ゲートとして実装したところ、
**bpm 検出が 123.05 → 129.2 に改善**した（休符ゲート導入前後の比較）。
リズム的な無音はビートトラッカーに有利に働く — 「演奏の明瞭さ」が
「観測の精度」を直接押し上げるという、制御トラック（K 系列）にも通じる知見。

## 5. PoC 1–5 への対応

| PoC | 検証内容 | 本デモでの状態 |
|---|---|---|
| PoC 1 | Score が書ける | Midnight Signal YAML がループの単一入力 |
| PoC 2 | Prompt に変換 | `generated_prompt.txt`（決定論的） |
| PoC 3 | Layer Manipulation | 未実施（C5）。ただし style 分離が下準備になる |
| PoC 4 | 複数レンダラ | 未実施（C6）。演奏者は事実上の最小ローカルレンダラ |
| PoC 5 | RPE Feedback | **決定論パスで実証**: 針が演奏差を連続量で捉えた（§3）。確率的生成器での再現と Score 修正ループ（RepairScore）は未実施（§6 / C5） |

## 6. 制限と次のステップ

- **外部生成器の確率性は未検証**。本デモの演奏者は決定論的であり、Suno/Udio の
  「同一プロンプト → 別演奏」のばらつきはまだ観測していない。手動ループの手順:
  1. `examples/composition/midnight_signal/generated_prompt.txt` を Suno/Udio に投入
  2. 生成音源を `svprpe audit examples/composition/midnight_signal/composition_score.yaml track.mp3` にかける
  3. 針を読み、Score（または生成リトライ）を調整して再監査
- 計画の手順 5「レポートに基づいて Score を修正」は、本デモでは「Score 不変・演奏が
  変わる」方向で代替した。Score 側を動かす版（作曲遂行の修正ループ）は C5 と合流させる。
- 演奏者のキーワードヒューリスティックは Midnight Signal の語彙に合わせた最小実装。
  別 Score を演奏させる場合は語彙の追加が必要になりうる。
