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

## package 共有と処置（A/B）provenance の切り分け

`package/performance_package.json` は **A/B 両セル共通の**コンパイル済みハンドオフ
（`svprpe package` の単一実行結果）であり、`prompt.section_tags`（section-tag
script）を含む。A/B の実際の処置分岐——この script を Suno UI の Lyrics 欄へ貼るか
（セル A）空のままにするか（セル B）——は **package の外側**、すなわち人間操作層
（`order_sheet.md` の "Cell A — tags" / "Cell B — no tags" 節）で行われた。package
自体はどちらのセルの操作を反映したものでもなく、両セルが参照する単一の正典
ハンドオフである。

したがって B セル（`suno_B1`/`suno_B2`）の観測 JSON に記録された `package_sha256`
は、「この package の正典 anchor（manifest 連鎖）に対して `svprpe observe` を
実行した」という歴史的事実の verbatim 記録であって、「この package の
`prompt` 全体（`section_tags` 含む）が B の生成入力として Suno に渡された」こと
を意味しない——B の生成入力は Style フィールドのみで、Lyrics 欄は空だった
（`order_sheet.md` 参照）。package/report の sha256 連鎖単体からは A/B の処置
を判別できない。

処置（treatment/control）の provenance の正本は `takes_memo.yaml` の各 take の
`cell` 欄（`suno_A1`/`suno_A2` → `A (tags)`、`suno_B1`/`suno_B2` →
`B (no tags)`）と `order_sheet.md` のセル定義であり、
`tests/test_ar4_suno_observed_fixture.py` がこの cell 割当を機械 pin する。

同様の切り分けが lyrics anchor の delivery status にも当てはまる。
`performance_package.json` の `anchor_statuses` は lyrics anchor（`hard`）を
`lyrics_text` チャネルで `delivered` と記録しているが、これは「`svprpe package` が
`identity/lyrics.txt` をコンパイル済みハンドオフに含めた」という宣言であって、
「Suno UI に歌詞が実際に貼付された」ことの記録ではない。実行手交では A/B 両セルとも
instrumental 固定であり、`identity/lyrics.txt` はどちらのセルの Lyrics 欄にも
貼付されていない（A セルの Lyrics 欄には `prompt.section_tags` の section-tag script
のみ、B セルの Lyrics 欄は空欄——`order_sheet.md` の Cell A / Cell B 節参照）。
実行手交と package の `channel_artifacts` との差分（どの channel が実際に UI へ
貼付されたか／されなかったか）は `handoff_deviations.yaml`（`order_sheet.md` /
`takes_memo.yaml` からの機械転記のみ・推定補完なし）が正本であり、
`tests/test_ar4_suno_observed_fixture.py` が package sha256・channel 集合・cell
割当の整合を機械 pin する。

## 観測の要点（詳細は `docs/arrangement_identity_planning.md` の 2026-07-19 dated エントリ）

- 4 take とも harmony/structure が `not_observed`/`deferred`、
  `sequence_exact_match: false`
- structure `position_match_rate`: tags セル A = {0.25, 0.5} vs no-tags セル
  B = {0.125, 0.375}（記述統計のみ）
- 尺: A = 191.0s / 178.2s vs B = 84.4s / 94.6s — 観測された A/B の尺差の記録のみ
  （非 canonical・実生成順 B1,B2,A1,A2 の順序交絡あり・n=2。因果・効果の主張なし）
- harmony: 4 take とも `full_cycles=0`（正典コード進行は 1 cycle も非再現）だが
  観測コードは C minor 近親圏
