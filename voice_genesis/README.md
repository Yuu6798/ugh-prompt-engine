# voice_genesis — UGH Voice Genesis Engine v0.2（仮想検証プログラム成果物）

## 目的

本ディレクトリは、決定論的・学習フリーの日本語歌唱合成エンジン
「UGH Voice Genesis Engine v0.2」の仮想検証プログラム（VT: Virtual Test）
一連のサイクルで得られた成果物一式を、リポジトリへ自己完結サブモジュール
として格納したものである。

検証プログラムは以下を段階的に実証した:

1. **物理合成器の妥当性**（VT-1〜3、`harness/`）: 声道フィルタ・声門源
   モデルの grip（Genome パラメータ操作が意図通り音響へ反映されるか）の
   実測検証
2. **試作品1号（PoC）**（`proto1/`）: Genome→レンダ→計測→監査→registry
   の因果チェーンの一気通貫実証
3. **歌唱合成 + Genesis Graph 探索 + 子音明瞭度デバッグ**（`singer/`）:
   日本語歌唱としての成立（Phase 2）、Genome の作り分けによる歌手識別
   （S2）、探索による新歌手の鍛造（S3〜S4, Genesis Graph v0）、cross-song
   実証（S5）、耳駆動の子音明瞭度デバッグラダー（S6〜S9、/r//h//n//m/の
   知覚手がかり修正）

各サイクルの詳細な設計判断・実測値・耳判定記録・未解決課題は
`singer/results_s*/`・`proto1/results_*/`・`harness/results*/` 配下の
`.md`/`.json` レポートに全て記録されている（下記「主要レポート索引」参照）。

## svp-rpe への非依存宣言

本ディレクトリのコードは **svp-rpe 本体（リポジトリ直下 `src/` パッケージ）
を一切 import しない**。逆方向（svp-rpe 側から voice_genesis を import
する）も存在しない。両者は完全に独立している（機械確認: svp-rpe の
パッケージ名を本ディレクトリ全体から検索して 0 件であることを確認済み）。

svp-rpe との関係は「同一リポジトリに同居する、設計思想・語彙の一部を
共有する独立プロジェクト」のみである。例えば決定論・凍結ファイル運用・
underspec_log による逸脱記録といった開発規律は svp-rpe の慣行を参考に
しているが、コード資産・依存関係の共有は一切ない。

## 3層構成

```
voice_genesis/
├── harness/    VT-1/2/3 仮想検証ハーネス（声道フィルタ・声門源の grip 実測）
├── proto1/     試作品1号: Genome→レンダ→計測→監査→registry の一気通貫実証
└── singer/     歌唱合成本体: R0.9 エンジン + Genesis Graph 探索 + 子音修正
```

- `harness/` は `proto1/`・`singer/` の両方から無改変で import される
  基盤モジュール（`measure.py`/`measure_v2.py`/`measure_v3.py`/
  `voice_r0.py` 等）と、各検証サイクル（VT-1〜VT-3、v0.3〜v0.6）の
  設計メモ・実測レポートを含む
- `proto1/` は Genome スキーマ（`genome.py`）・R0.1 レンダラ橋渡し
  （`bridge.py`）・plausibility 判定・reference-set/linkability 監査
  （`reference_set.py`）・registry を実装する
- `singer/` は R0.9 決定論歌唱エンジン（`render_song.py` 系列 v0-v5）・
  S5 機械ゲート（`gate_checks.py` 系列 v0-v2）・Genesis Graph 多世代探索
  （`genesis_v0/v1/v2.py`）・子音明瞭度検査（`consonant_checks_v2〜v5.py`）
  を実装する

内部の相互参照（`singer/` → `proto1/` → `harness/`）はディレクトリ名の
相対パス解決（`Path(__file__).resolve().parent.parent / "harness"` 等）
で行われている。旧配置（scratchpad 上の `vt_harness/`）からの移植に伴い、
ディレクトリ名を `harness/` へ短縮した箇所のみ文字列を修正した
（ロジック・相対パス階層は不変）。

## WAV は非同梱（決定論再生成可能）

`results_*/` 配下の `.wav` 音声ファイルは**同梱していない**（全 39 個、
計 22MB）。理由: 本エンジンは完全決定論（同一 Genome + 同一 seed →
同一バイト列。`gate4_determinism` で全サイクル実測確認済み）であり、
音声そのものは以下のコマンドで誰でも再生成できるため、リポジトリ肥大化
を避けた。

```bash
cd voice_genesis
python3 -c "
import sys; sys.path.insert(0, 'singer'); sys.path.insert(0, 'proto1')
import render_song as rs
result = rs.render_sakura(rs.voice_a())   # 例: voice_A のさくらさくら
import soundfile as sf
sf.write('/tmp/sakura_voiceA.wav', result.wav, result.sr)
"
```

各サイクル最終版（S9, `render_song_v5.py` 系列）で genesis3/voice_C の
2曲（さくらさくら・うみ）を再生成する場合は
`singer/results_s9/nasal_place_report.md` 記載のパラメータ・
`singer/render_song_v5.py` の `render_sakura_v5()` を用いる。

## 全ゲート成立状況（要約）

| サイクル | 内容 | 判定 | 記録 |
|---|---|---|---|
| Phase 2 | 日本語歌唱としての成立 | ✓ 成立 | `singer/results_s1/phase2_gate_record.md` |
| S2 | Genome 作り分けによる歌手識別（voice_C/D） | ✓ 成立 | `singer/results_s2/s2_gate_record.md` |
| S3〜S4 | Genesis Graph v0（探索による新歌手鍛造、genesis3） | ✓ 成立 | `singer/results_s4/s4_gate_record.md` |
| S5 | cross-song identity 実証（うみ×さくら） | ✓ 成立（耳）/ 7/8（機械） | `singer/results_s5/crosssong_report.md` |
| S6〜S9 | 子音明瞭度デバッグラダー（/r//h//n/(→/w/→/m/)/最終) | ✓ 成立 | `singer/results_s9/s9_gate_record.md` |

S6〜S9 は耳判定→機械診断→単一機序修正→検出力証明→非退行確認、を
反復する「耳駆動デバッグラダー」として運用され、S9 で全ての音韻論的
所見（子音の様式・調音位置弁別）が解消し、identity/gate 非退行を維持
したまま完結した（`singer/results_s9/s9_gate_record.md` の教訓節参照）。

未解決の引き継ぎ事項（次段階候補）:
- voice_D は gate6-v2（score-informed QC）で不合格のまま
  （`singer/results_s4/safe_box_v2.md`）。voice_C のみ品質証明を保持
- 適応 GAIN_FLOOR の本格実装（`formant_scale` を identity 軸として
  復権させる前提、`singer/results_s4/genesis_report_c.md`）
- identity 計測（E1/E2 スタンドイン embedding）が子音修正のたびに
  揺れる問題 — 母音核限定 identity 計測への改訂が残課題
  （`singer/results_s9/s9_gate_record.md` 教訓3）

## 主要レポート索引

| レポート | 内容 |
|---|---|
| `harness/design_review_report.md` | VT ハーネス全体の設計レビュー総括 |
| `harness/v06_design_memo.md` | 最新版（v0.6）の声道フィルタ設計メモ |
| `proto1/PROTOTYPE1_COMPLETION_REPORT.md` | 試作品1号 完了報告 |
| `proto1/results_final/acceptance_report.md` | 試作品1号 受け入れ判定 |
| `singer/r09_design_memo.md` | R0.9 歌唱エンジン設計メモ |
| `singer/results_s1/phase2_gate_record.md` | Phase 2（日本語歌唱成立）耳判定 |
| `singer/results_s2/identity_report.md` | S2 Genome 知覚的分離度強化の実測 |
| `singer/s3_genesis_design_memo.md` | Genesis Graph v0 設計メモ |
| `singer/results_s4/genesis_report_c.md` | S4 gate6 score-informed QC 再設計 + genesis3 鍛造 |
| `singer/results_s4/safe_box_v2.md` | S4 安全域ボックス実測（voice_B/D 再監査含む） |
| `singer/results_s5/crosssong_report.md` | S5 cross-song identity 実証 + 証明書衛生 |
| `singer/results_s9/nasal_place_report.md` | S9 鼻音調音位置 locus 実装（子音デバッグラダー完結） |
| `singer/results_s9/s9_gate_record.md` | S6〜S9 耳駆動デバッグラダー全記録 + 教訓 |

各 `results_s*/underspec_log_s*.md` にサイクルごとの仕様逸脱・補充判断が
記録されている。

## テストの実行

`voice_genesis/` のテスト（`proto1/tests/`・`singer/tests/`）は音声合成を
伴う重いテストのため、リポジトリルートの `pytest -q`（`testpaths =
["tests"]` により `tests/` 配下のみを収集）には**含まれない**。個別に
実行すること:

```bash
cd /home/user/ugh-prompt-engine
python3 -m pytest voice_genesis/proto1/tests voice_genesis/singer/tests -q
```

## Lint（ruff）

`ruff check .` はリポジトリ全体を対象とし、`voice_genesis/` も通常どおり
lint 対象に含めている（除外していない）。唯一の例外は
`voice_genesis/harness/measure.py` と `voice_genesis/harness/vt3_v4.py`
（VT ハーネスの複数サイクルから「凍結・無改変」前提で import される
基盤ファイル）に残る F841（未使用変数）2件で、これは
`pyproject.toml` の `[tool.ruff.lint.per-file-ignores]` で当該2ファイル
のみ個別除外している（凍結ファイルのバイト列を lint 整形のために
変更しないため。`voice_genesis` 全体を除外する方式は採らなかった）。
