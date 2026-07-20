# AR4 実 Suno 検収デモ（2026-07-19）

2026-07-19 に実施した、**実 Suno 5.5** 生成物での AR4（`svprpe observe`）**初観測**の
committed 記録。実 Suno の Custom mode で instrumental 固定・Style 欄同一のまま
Lyrics 欄の section-tag script 有無のみを差し替えた 2 セル（A=tags / B=no tags）×
2 take の A/B 検収デモであり、**非 canonical 探索**（n=2×2、記述統計のみ）である。
事前登録ブロックは `order_sheet.md` 冒頭にある。ABBA なし、時刻証跡連鎖なし、
効果量の主張はしていない。

## パス対応表

`order_sheet.md` 本文中のパスは当時の scratchpad レイアウト
（`scratchpad/suno_ar4_demo/`）を指す**歴史的記録**であり、committed レイアウト
（このディレクトリ）とは以下のように対応する。

| scratchpad (`order_sheet.md` 内表記) | committed (このディレクトリ) |
|---|---|
| `scratchpad/suno_ar4_demo/inputs/` | `inputs/` |
| `scratchpad/suno_ar4_demo/package/` | `package/` |
| `scratchpad/suno_ar4_demo/observed/` | `observed/` |
| `scratchpad/suno_ar4_demo/audio/takes_memo.yaml` | `takes_memo.yaml`（直下に移動） |
| `scratchpad/suno_ar4_demo/audio/*.mp3` | **非コミット**（下記参照） |

`inputs/` `package/` `observed/` はほぼ同型のまま移送している。`audio/` のみ
音源ファイル自体を含まないため非コミット。

## 音源について

音源 4 本（`suno_A1.mp3` / `suno_A2.mp3` / `suno_B1.mp3` / `suno_B2.mp3`）は
**非コミット**。provenance は `takes_memo.yaml`（各 take の sha256 / 収録時刻 /
元アップロードファイル名）と、各観測 JSON の `generated_artifact.sha256` に
記録されている。Google Drive 等への退避は follow-up 課題。

## 再検証（マシン非依存）

repo root から次のコマンドで D-3 provenance 連鎖を再検証できる:

```bash
svprpe verify examples/arrangement/midnight_signal/observed/suno/package/performance_package.json \
  --manifest examples/arrangement/midnight_signal/observed/suno/inputs/identity_manifest.demo.yaml
```

### 実行結果（fixture 化時点の実測、2026-07-20 実測 `date -u`）

- 実行日時（UTC）: 2026-07-20T06:45:43Z
- exit code: `0`
- チェック数: `checked 38, failed 0`（V1 package load / V2 compilation report /
  V3 identity manifest chain / V4 channel_artifacts の全項目が `ok`）

生出力は開発時の作業記録として `records/impl_out/verify_output.txt`
（非コミットの実装作業ディレクトリ）に保存済み。

## fixture 整合

このディレクトリの内容整合（pin 一致・repo 正本とのバイト同一・非 canonical
探索の位置づけの pin）は `tests/test_ar4_suno_observed_fixture.py` が enforce する。

## 観測の要点（詳細は `docs/arrangement_identity_planning.md` の 2026-07-19 dated エントリ）

- 4 take とも harmony/structure が `not_observed`/`deferred`、
  `sequence_exact_match: false`
- structure `position_match_rate`: tags セル A = {0.25, 0.5} vs no-tags セル
  B = {0.125, 0.375}（記述統計のみ）
- 尺: A = 191.0s / 178.2s vs B = 84.4s / 94.6s（n=2 につき方向の記録のみ）
- harmony: 4 take とも `full_cycles=0`（正典コード進行は 1 cycle も非再現）だが
  観測コードは C minor 近親圏
