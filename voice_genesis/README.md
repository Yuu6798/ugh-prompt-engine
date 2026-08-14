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

## 移行時パス対応表（旧 `vt_harness/` → 現 `harness/`）

scratchpad 上の検証プログラムは `vt_harness/` というディレクトリ名で
運用されていた。リポジトリへの統合（このディレクトリの新設）に伴い
`harness/` へ改称している。対応関係は単純な接頭辞置換である
（PR#261 レビュー C4 対応）:

| 旧パス（scratchpad, `vt_harness/` 起点） | 現パス（リポジトリ, repo root 起点） |
|---|---|
| `vt_harness/<file>` | `voice_genesis/harness/<file>` |
| 例: `vt_harness/results_v6/grip_report_v6.json` | `voice_genesis/harness/results_v6/grip_report_v6.json` |
| 例: `vt_harness/results_v3/grip_report_v3.json` | `voice_genesis/harness/results_v3/grip_report_v3.json` |

適用範囲:

- **機械可読ファイル（`.py`/`.json`/`.jsonl`）**: 全て `harness/`（sibling
  相対パス）または `voice_genesis/harness/...`（repo root からの参照パス）
  へ修正済み。`grep -r "vt_harness" voice_genesis/ --include="*.py"
  --include="*.json" --include="*.jsonl"` は 0 件
- **`.md` の歴史的記録（設計メモ・`underspec_log_*.md`・ゲート判定記録等）**:
  意図的に**書き換えていない**。これらは実行当時のディレクトリ名
  （`vt_harness/`）をそのまま記録した歴史的記録であり、後から現在の
  ディレクトリ構成に合わせて改変すると、当時の実測・判断の一次記録性
  が損なわれるため。読む際は本表で `harness/` へ読み替えること

## reference-set の版と stale_audit

`reference_set.py` の sidecar 様式は `reference-set/<version>` として
版管理される（`ReferenceSetGallery.sha256` = 内容 sha256）。フィールドを
追加・変更したら schema_version を bump するのが規律であり、対応して
`reference_set_hash` も変わる（PR#261 レビュー R4 で `reference-set/0.1`
→ `0.2` へ bump し、チャンス帯手続きパラメータ `chance_seed_base` /
`n_permutations` / パーセンタイルを sidecar と hash 被覆の両方へ追加した。
旧版はこれらを変えても hash が不変で、`e1_pass`/`e2_pass` を左右する
判定手続きの違いが reference_set_hash に現れない欠陥があった）。

**版が変わると、旧版の gallery に対して合格していた過去の linkability
監査は新版に対しては自動的に stale 扱いになる。** これは不具合ではなく
`LinkabilityAuditLog.mark_stale(current_reference_set_hash)` が意図して
実装している再監査トリガーの挙動そのものである（
`report.reference_set_hash != current_reference_set_hash` の全件に
`stale_audit=true` を立てる）。したがって:

- 0.1 時代に生成済みのコミット済み成果物（`proto1/results_final/genome_registry.jsonl`
  の各エントリの `audit.reference_set_hash`、`proto1/results_final/e2e_run.json`
  の `reference_set.sidecar`）は、0.1 の hash を持つ歴史的記録として
  **書き換えない**。新版 gallery に対する再監査は別途 `build_reference_set()`
  を再実行して行う（本 README 上部の WAV 再生成コマンドと同様、決定論的に
  再現可能）
- 「参照集合の版が変わったら過去の監査結果が古くなる」という性質は
  sidecar を版管理された成果物として扱う設計上、意図された挙動である

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

**既知の限界（既存成果物との差分）**: `proto1/proto1_demo.py` が出力する
`e2e_run.json` の `selected_pass_genome_for_wav.wav_paths` は、PR#261
レビュー R11（WAV ファイルバイトの sha256 digest + 2 回書き比較による
`soundfile.write()` 自体の決定論性確認）以降、各 WAV エントリが文字列
（相対パス）ではなく `{"path", "sha256", "write_determinism_check"}` の
構造化 dict になっている。コミット済み `proto1/results_final/e2e_run.json`
は R11 より前に生成された歴史的記録であり、旧スキーマ（文字列）のまま
書き換えていない。新スキーマは次回以降の `proto1_demo.py` 実行から反映される。

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

## 検証境界の終端宣言（ロード経路・公開経路の census）

PR#261 のレビューは C1–C6 → R1–R7 → R8–R11 → R12–R13 の 5 ラウンドに
わたり、「ローダー/来歴/公開」系の同型の穴（schema 未検証・schema キー
欠落時のデフォルト補完・content hash 未検証・pass/fail 宣言の未再計算・
再計算前提となる実測値自体の定義域未検証・親の存在/順序不変条件・staging
の不完全さ）を逐次指摘し続けた。指摘の再発を止めるため、`voice_genesis/`
内で永続化
データを**読み込む**（デシリアライズしてオブジェクトへ復元する）経路、
および**正本を公開する**（成果物ファイルを書き換える）経路を機械的に
全列挙する（`grep -rln "json\.load(\|json\.loads(" voice_genesis
--include="*.py" | grep -v "/tests/"` で確認できるロード経路が母集合）。

### ロード経路（永続化データ → オブジェクトへの復元）

| ファイル::関数 | 読む対象 | 検証内容 | 状態 |
|---|---|---|---|
| `proto1/genome.py::from_dict()` | Genome JSON document | schema_version キーの**存在**を要求し欠落を拒否 (R12) / schema_version 一致 (C6) / physio_range 再計算一致 (C5) / 全フィールド型検証 | **検証済み** |
| `proto1/registry.py::_entry_from_dict()`（`GenomeRegistry.load_all()` 経由） | registry JSONL 1 行 | registry_schema キーの**存在**を要求し欠落を拒否 (R12) / registry_schema 一致 (C5/C6 掃討) / 埋め込み genome を `from_dict()` で検証 (R3a) / genome_id と content hash の一致 (R3b) / エントリ側 audit と genome.audit の一致 (R3c) / `load_all()` 内での重複 genome_id 検出 (R5) / 親の存在・自エントリより前に出現 (R10)。renderer_version/feature_set_version は同型 grep 掃討 (R12) で発見したが、構造解釈を左右しない由来メタデータのためデフォルト補完のまま境界宣言 | **検証済み** |
| `proto1/reference_set.py::_report_from_dict()`（`LinkabilityAuditLog.load_all()` 経由） | 監査ログ JSONL 1 行 | pass/fail 再計算 (R9) より先に、その入力（類似度・チャンス帯 p95）が有限かつコサイン類似度の定義域 [-1.0, 1.0] 内であることを検証し域外・非有限を拒否 (R13) / 保存済み実測値から e1_pass/e2_pass/overall_pass を再計算し宣言値と照合 (R9) | **検証済み** |
| `harness/vt3_v5.py::restate_from_v4()` | `results_v4/grip_report_v4.json` | 検証なし（生 dict indexing） | **凍結対象外**（harness/ は凍結・無改変の歴史的検証コード。書き換えると当時の実測の一次記録性が損なわれる） |
| `harness/vt3_v6.py::restate_from_v4()` | 同上 | 同上 | **凍結対象外**（同上） |

### 公開経路（正本ファイルの書き換え）

| ファイル::関数 | 書く対象 | 検証内容 | 状態 |
|---|---|---|---|
| `proto1/proto1_demo.py::_publish_outputs()` / `_publish_or_fail_closed()` | `genome_registry.jsonl` / WAV 2 種 / `e2e_run.json` の正本置換 | 全成果物を staging へ揃えてから一括 `os.replace` (R1) / 決定論比較（genome 全 diff + WAV ファイル書き出しの決定論性）不成立時は publish 自体を呼ばず失敗診断を非正本パスへ (R8) / WAV ファイルバイトの sha256 digest 記録 + 2 回書き比較 (R11) | **検証済み** |
| `proto1/registry.py::GenomeRegistry.append()` | registry JSONL への 1 行追記 | op 許容値検証 / 重複 genome_id 拒否 (C3) / parents の各 ID が既に registry に存在することを検証 (R10a) | **検証済み** |
| `proto1/reference_set.py::LinkabilityAuditLog.append()` / `_rewrite()` | 監査ログ JSONL | — | **対象外**（常に `audit_linkability()` が内部で構築した `LinkabilityAuditReport` オブジェクトのみを受理し、外部由来の未検証データを直接書き込む経路が存在しないため、書き込み時点での改ざん検証は不要。読み込み時の検証は上表 R9 でカバー） |
| `proto1/reference_set.py::ReferenceSetGallery.sidecar_dict()` | `reference-set/0.2` sidecar dict（呼び出し元が JSON へ埋め込む） | — | **対象外**（書き出し専用。対応する読み込みローダーが本コードベースに存在しない。C5/C6 掃討・R9 対応時に確認済み） |
| `proto1/results_p1/_generate_report_data.py` | `results_p1/report_data.json` 等 | — | **対象外**（一回限り実行済みの歴史的レポート生成スクリプト。再実行して正本を差し替える経路も、生成物を再ロードする経路も存在しない） |
| `harness/*.py` の各 `main()`（`vt1_v2/v3.py`・`vt2_*.py`・`vt3_*.py` 等） | `results_v*/*.json` への一回限りの書き出し | — | **凍結対象外**（harness/ は凍結・無改変の歴史的検証コード） |
| `singer/*.py` | — | — | **該当コードなし**（`grep -rln "json\.dump\|write_text.*json\|\.jsonl\"" voice_genesis/singer/*.py` は 0 件。`results_s*/*.json` は同様の一回限り生成の歴史的記録） |

**終端宣言**: 上記 11 行のうち、生成・公開系（proto1 の 3 経路）は全て
検証済み。harness/ の 3 経路（読み込み 2 + 書き込み複数）は凍結・無改変
原則により明示的に対象外。write-only で対応する loader が存在しない
経路（reference_set の sidecar・一回限りのレポート生成スクリプト・
singer/）も明示的に対象外。**全行が「検証済み」または明示的な境界宣言に
分類されたため、C1–C6/R1–R11/R12–R13 の 5 ラウンドにわたる「ローダー/
来歴/公開」系残穴の指摘サイクルをここで終端とする。** 今後同種の指摘が
来た場合は、
本表に新しい行を追加できるかどうか（＝これまで見落としていた経路か）を
まず確認すること。既存行の再指摘であれば、当該行の「検証内容」列に
記載済みの対処で十分か、対処自体に不備があるかを個別に判断する。

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
