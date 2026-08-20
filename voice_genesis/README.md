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

- 版が変わった直後のコミット済み成果物（`proto1/results_final/genome_registry.jsonl`
  の各エントリの `audit.reference_set_hash`、`proto1/results_final/e2e_run.json`
  の `reference_set.sidecar`）は、原則としてその時点の hash を持つ歴史的
  記録として**書き換えない**。新版 gallery に対する再監査は別途
  `build_reference_set()` を再実行して行う（本 README 上部の WAV 再生成
  コマンドと同様、決定論的に再現可能）。**2026-08-14（PR#261 R34）**:
  この原則の唯一の明示的な例外として、R24–R36 の検証強化サイクル完了後
  に `results_final/` 一式（`e2e_run.json` / `genome_registry.jsonl`）を
  現行コードで意図的に再生成し正本を更新した（下記「WAV は非同梱」節の
  既知の限界も合わせて解消）。現在のコミット済み成果物は
  `reference-set/0.2` の hash を反映している
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

`proto1/proto1_demo.py` が出力する `e2e_run.json` の
`selected_pass_genome_for_wav.wav_paths` は、PR#261 レビュー R11（WAV
ファイルバイトの sha256 digest + 2 回書き比較による `soundfile.write()`
自体の決定論性確認）以降、各 WAV エントリが文字列（相対パス）ではなく
`{"path", "sha256", "write_determinism_check"}` の構造化 dict になって
いる。**2026-08-14（PR#261 R34）**: コミット済み `proto1/results_final/
e2e_run.json` は R11 より前に生成された旧スキーマ（文字列）の歴史的記録
だったが、R24–R36 の検証強化サイクル完了後のコードで正本一式を再生成し、
新スキーマ（構造化 dict + WAV 本体の sha256 収載）へ更新した。WAV 本体
そのものは引き続きリポジトリに同梱しない（サイズ + sha256 収載により
バイト列レベルの検証は可能なため）。

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
| `foundry/planb/DESIGN_PLANB_poc.md` | Plan B PoC の実験契約（Identity × Performance 分離・事前登録 TRF ゲート・被覆申告） |
| `foundry/planb/results_pb0/pb0_record_2026-08-20.md` | Plan B PoC 第 1 走行（代替ドナーでの機構検証。受入 6 条件中 3 達成 / 2 は実資産・耳律速で未達） |

各 `results_s*/underspec_log_s*.md` にサイクルごとの仕様逸脱・補充判断が
記録されている。

## 検証境界の終端宣言（ロード経路・公開経路の census）

PR#261 のレビューは C1–C6 → R1–R7 → R8–R11 → R12–R13 → R14–R16 →
R17–R19 → R20–R21 → R22–R23 → R24–R25 → R26 → R27–R28 → R29–R30 →
R31–R33 → R34–R36 → R37–R38 の 16 ラウンドにわたり、「ローダー/来歴/公開」系
および「検出ロジック」系の同型の穴（schema 未検証・schema キー欠落時の
デフォルト補完・全セクション/リーフフィールド欠落時のデフォルト補完・
float リーフの非有限値（NaN/inf）未検証・content hash 未検証・エントリ内の
重複表現フィールド間の不整合・列挙型フィールド（op）の読み込み側検証の
非対称性・閉じた語彙フィールド（source_mode）の読み込み側域外値未検証・
関係フィールド間の不変条件（op × parent 数）の読み込み側未検証・内容アドレス
（report_id）の読み込み側未検証・内容アドレスフィールドの重複検出欠如
（append-only ログの一意性不変条件未検証）・pass/fail 宣言の未再計算・
再計算前提となる実測値自体の定義域未検証・親の存在/順序不変条件・
staging の不完全さ（固定ファイル名による併走実行の踏みつけ・正本
publish の排他制御欠如）・書き戻し処理の非アトミック性（中断時の全損
リスク）・**追記(append)経路の非アトミック性（中断時の部分行破損による
正本 JSONL 恒久破損リスク）**・**read-modify-replace 全区間の未直列化
（プロセス間排他の欠如による読み込み前提の陳腐化・lost update レース）**・
歴史的正本レポートの開示文言の陳腐化・**「実出力だけを走査し期待値集合と
照合しない」検出力欠陥**・**最近傍/最良値探索ループでの NaN 距離の
fail-open**・**nanmedian 等の暗黙除外・フィルタ後リストの非空判定による
不完全証拠 PASS**・**期待件数チェック欠如（件数そのものの退行を見ない）
による不完全証拠 PASS**・**プローブ結果数の期待値未照合（`zip()` の黙落・
0/1 本しか無い場合の隣接差分空リストによる偽 PASS）**・**必須成果物
が生成できない run の publish 未門番化（決定論とは独立の fail-closed
条件の欠落）**・**書き込み経路が読み込み側専用の検証を経由しない
書読非対称性**）を逐次指摘し続けた。指摘の再発を
止めるため、`voice_genesis/` 内で永続化データを**読み込む**（デシリア
ライズしてオブジェクトへ復元する）経路、**正本を公開する**（成果物
ファイルを書き換える）経路、および（R20–R21 で追加）レンダリング結果
に対し**その場で pass/fail を判定する検出ロジック**経路を機械的に全
列挙する（`grep -rln "json\.load(\|json\.loads(" voice_genesis
--include="*.py" | grep -v "/tests/"` で確認できるロード経路が母集合。
検出ロジック経路は R20–R21 の指摘対象を機縁に追加した第三の区分であり、
永続化 JSON の grep 母集合には含まれない）。

### ロード経路（永続化データ → オブジェクトへの復元）

| ファイル::関数 | 読む対象 | 検証内容 | 状態 |
|---|---|---|---|
| `proto1/genome.py::from_dict()` | Genome JSON document | schema_version キーの**存在**を要求し欠落を拒否 (R12) / schema_version 一致 (C6) / 全 8 セクション（source/resonance/noise/register/microprosody/range/physio_range/audit）と各リーフフィールドのキー**存在**を要求し欠落（切り詰め payload）を拒否 (R16。デフォルト補完は `build_genome()` 等の明示的コンストラクタ経路のみに限定) / 全 float リーフ（`formant_offsets` 等リスト内要素を含む）の NaN/inf を `_as_float` で一括拒否し有限値のみ受理 (R18) / `source.source_mode` を builder（sampler.py）発行の閉じた語彙 `SOURCE_MODES = (modal, breathy, pressed)` で検証 (R26。schema 内の他の文字列フィールドを掃討したが、`physio_range.violated_bounds` は C5 の再計算一致検証で既に間接的に閉じた語彙が保証されているため対象外と境界宣言) / physio_range 再計算一致 (C5) / 全フィールド型検証 | **検証済み** |
| `proto1/registry.py::_entry_from_dict()`（`GenomeRegistry.load_all()` 経由） | registry JSONL 1 行 | registry_schema キーの**存在**を要求し欠落を拒否 (R12) / registry_schema 一致 (C5/C6 掃討) / 埋め込み genome を `from_dict()` で検証 (R3a、R16・R18 強化が読み込み経路にも波及) / genome_id と content hash の一致 (R3b) / エントリ側 audit と genome.audit の一致 (R3c) / エントリ直下 version と埋め込み genome.schema_version の一致 (R14) / op を `VALID_OPS`（`append()` と同一の許容値集合）で検証 (R22。`append()` 側は元から検証していたが `_entry_from_dict()` は宣言値を無条件に信頼していた非対称性を解消) / op 別の parent 数不変条件（sample=0/mutate=1/crossover=2）を検証 (R24。同じく `append()` 側は書き込み時に検証していたが読み込み側は無検証だった非対称性を解消) / `load_all()` 内での重複 genome_id 検出 (R5) / 親の存在・自エントリより前に出現 (R10)。renderer_version/feature_set_version は同型 grep 掃討 (R12) で発見したが、構造解釈を左右しない由来メタデータのためデフォルト補完のまま境界宣言 | **検証済み** |
| `proto1/reference_set.py::_report_from_dict()`（`LinkabilityAuditLog.load_all()` 経由） | 監査ログ JSONL 1 行 | report_id を {genome_id, reference_set_hash} から再計算し宣言値と照合、内容アドレス改ざんを拒否 (R19) / pass/fail 再計算 (R9) より先に、その入力（類似度・チャンス帯 p95）が有限かつコサイン類似度の定義域 [-1.0, 1.0] 内であることを検証し域外・非有限を拒否 (R13) / 保存済み実測値から e1_pass/e2_pass/overall_pass を再計算し宣言値と照合 (R9) / `load_all()` 内で report_id の重複を検出し拒否 (R33。registry.py の R5 genome_id 重複検出と同型。report_id は R19 の内容アドレスのため重複は「同じ監査を指す 2 通りの主張」の矛盾を意味する) | **検証済み** |
| `harness/vt3_v5.py::restate_from_v4()` | `results_v4/grip_report_v4.json` | 検証なし（生 dict indexing） | **凍結対象外**（harness/ は凍結・無改変の歴史的検証コード。書き換えると当時の実測の一次記録性が損なわれる） |
| `harness/vt3_v6.py::restate_from_v4()` | 同上 | 同上 | **凍結対象外**（同上） |

### 公開経路（正本ファイルの書き換え）

| ファイル::関数 | 書く対象 | 検証内容 | 状態 |
|---|---|---|---|
| `proto1/proto1_demo.py::_publish_outputs()` / `_publish_or_fail_closed()` | `genome_registry.jsonl` / WAV 2 種 / `e2e_run.json` の正本置換 | 全成果物を staging へ揃えてから一括 `os.replace` (R1) / 決定論比較（genome 全 diff + WAV ファイル書き出しの決定論性）不成立時は publish 自体を呼ばず失敗診断を非正本パスへ (R8) / WAV ファイルバイトの sha256 digest 記録 + 2 回書き比較 (R11) / staging ファイル名を `_run_suffix()`（pid + uuid4 の一部）で per-run 一意化し併走 `main()` 同士の staging ファイル踏みつけを防止、加えて正本 publish 直前を `_publish_lock()`（O_EXCL 作成 + 終了時削除）で排他しロック存在時は `PublishLockError`→`SystemExit` で即拒否 (R25。registry_path の serialize は R2 の repo-relative 正本パスのまま・決定論比較対象からは元々除外済み) / `_publish_or_fail_closed()` の門番条件を `determinism_passed` から `publish_ready`（決定論一致 **かつ** F1-7 必須 WAV 成果物を生成できたこと）へ一般化し、全候補が linkability 監査不合格で `selected_key is None` のまま WAV が 1 件も生成できない run を、決定論とは独立の fail-closed 条件として publish 拒否 (R27。理由文字列は失敗要因を列挙して診断へ反映) | **検証済み** |
| `proto1/registry.py::GenomeRegistry.append()` / `_write_all_atomic()` | registry JSONL への 1 行追記 | op 許容値検証 / 重複 genome_id 拒否 (C3) / parents の各 ID が既に registry に存在することを検証 (R10a) / op 別の parent 数不変条件（sample=0/mutate=1/crossover=2）を検証 (R24。`_entry_from_dict()` にも同型検証を追加し読み込み経路の非対称性も防止) / 書き込み前に直列化 → `_entry_from_dict()` によるラウンドトリップを試し、`load_all()` が拒否するであろうエントリ（例: 呼び出し元が frozen dataclass を直接組み立てて `source_mode="robotic"` 等の不正値を持つ genome を渡した場合）を書き込み前に拒否し書読対称性を保証 (R28。将来 append 高頻度化時の性能注記を docstring に明記) / `_entry_from_dict()` の seed フィールドを `_as_optional_int()`（None または int、bool は明示排除）で実行時型検証 (R29。`RegistryEntry.seed: Optional[int]` は型注釈のみで従来無検証だった。append() 側は R28 のラウンドトリップ検証が間接的にカバー) / 書き込み本体を旧 `self.path.open("a")` での write() から `_write_all_atomic()`（既存全件 + 新規エントリを同一ディレクトリの staging ファイルへ書いてから `os.replace()` で置換）へ変更 (R35。`a` モードの write() は書き込み途中の中断で正本末尾に破損した部分行が恒久的に残り得た。reference_set.py の `_rewrite()`（R17）と同型パターン。置換前の中断では旧ログが無傷のまま残り staging ファイルも掃除されることをテストで確認) / 検証（重複/親存在の `load_all()`）から書き込み（staging → `os.replace()`）までの read-modify-replace 全区間を、新設共通ヘルパー `filelock.file_lock()`（`<正本>.lock` への `O_CREAT\|O_EXCL`、R25 の `_publish_lock` と同型）で排他しプロセス間直列化 (R37。ロックなしだと検証時点では存在しなかった genome_id が、書き込み直前には別プロセスの割り込みで既に存在するようになっているレースが理論上あり得た)。ロック保持中の並行 append 模擬（ロックファイル事前作成 → 即座に `FileLockError`）と非退行（ロック解放後は従来どおり成功しロックファイルが残置しない）をテストで確認 | **検証済み** |
| `proto1/reference_set.py::LinkabilityAuditLog.append()` / `_rewrite()` / `mark_stale()` | 監査ログ JSONL | ラウンドトリップ検証（R30、下記）に先立ち、既存ログとの report_id 重複を軽量チェックで拒否 (R33。`load_all()` 側の重複検出と対称)。`append()` は書き込み前に直列化 → `_report_from_dict()` によるラウンドトリップを試し、`load_all()`/`mark_stale()` が拒否するであろうレポート（例: 呼び出し元が `LinkabilityAuditReport(...)` を直接構築して `e1_pass` を実測値と矛盾させた場合）を書き込み前に拒否し書読対称性を保証 (R30。registry.py の R28 と同型。旧来「常に `audit_linkability()` 内製オブジェクトのみ受理する設計だから内容検証不要」としていた前提を、直接構築された不整合オブジェクトが渡り得るケースに備えて実検証へ格上げ)。`_rewrite()`（`mark_stale()` が使う全件書き戻し）は旧 truncate-then-write（中断時に旧ログ全損の恐れ）から、同一ディレクトリへの staging ファイル書き込み + `os.replace()` によるアトミック置換へ変更 (R17)。置換前の中断では旧ログが無傷のまま残ることをテストで確認。`mark_stale()` の書き戻し経路は `load_all()` で得た既存集合の `stale_audit` のみを差し替える（report_id 不変・`append()` 非経由）ため R33 の重複検出と干渉しないことをテストで確認。`append()` 自体の書き込み本体も旧 `self.path.open("a")` での write() から、既存全件 + 新規レポートを `_rewrite()` へ渡す形へ変更 (R35。registry.py の `_write_all_atomic()` と同型の動機。既存の `_rewrite()`（R17）をそのまま再利用したため新規 staging ロジックの重複実装なし)。置換前の中断では旧ログが無傷のまま残り staging ファイルも掃除されることをテストで確認。`append()`（重複検出の `load_all()` から `_rewrite()` まで）・`mark_stale()`（読み改めの `load_all()` から `_rewrite()` まで）の両方を、registry.py と共通の `filelock.file_lock()` で排他しプロセス間直列化 (R37。ロックなしだと、`mark_stale()` が保持する古いスナップショットで丸ごと `_rewrite()` した際、区間途中で割り込んだ別プロセスの `append()` の新規行が正本から消え失せる lost update が理論上あり得た)。ロック保持中の並行 append/mark_stale 模擬（即座に `FileLockError`）と非退行をテストで確認 | **検証済み** |
| `proto1/reference_set.py::ReferenceSetGallery.sidecar_dict()` | `reference-set/0.2` sidecar dict（呼び出し元が JSON へ埋め込む） | — | **対象外**（書き出し専用。対応する読み込みローダーが本コードベースに存在しない。C5/C6 掃討・R9 対応時に確認済み） |
| `proto1/results_p1/_generate_report_data.py` | `results_p1/report_data.json` 等 | — | **対象外**（一回限り実行済みの歴史的レポート生成スクリプト。再実行して正本を差し替える経路も、生成物を再ロードする経路も存在しない） |
| `harness/*.py` の各 `main()`（`vt1_v2/v3.py`・`vt2_*.py`・`vt3_*.py` 等） | `results_v*/*.json` への一回限りの書き出し | — | **凍結対象外**（harness/ は凍結・無改変の歴史的検証コード） |
| `singer/*.py` | — | — | **該当コードなし**（`grep -rln "json\.dump\|write_text.*json\|\.jsonl\"" voice_genesis/singer/*.py` は 0 件。`results_s*/*.json` は同様の一回限り生成の歴史的記録） |

### 検出ロジック経路（レンダリング結果に対する即時判定、JSON 永続化を経由しない）

R20–R21 で判明した第三の穴の類型: 上記 2 表はいずれも「JSON を読み書き
する」経路を対象にしていたが、レンダリング結果（波形・embedding ベクト
ル）に対しその場で pass/fail を判定する検出ロジックにも、①「レンダラの
実出力だけを走査し、独立した期待値集合と照合しない」検出力欠陥、②最近傍
/最良値探索ループでの NaN 距離の fail-open、という同系統の穴があり得る
ことが分かった。

| ファイル::関数 | 判定対象 | 検証内容 | 状態 |
|---|---|---|---|
| `singer/gate_checks.py::gate3_consonant_existence()`（`gate_checks_v2.py::run_full_gates_v2()` 経由でも共有・独自実装なし） | レンダリング結果の子音サブセグメント | 楽譜側（`result.segments[*].note.mora.onset`、レンダラの実出力に依存しない独立した期待値源）から期待子音インスタンス集合を先に確定し、`subsegments_out` に対応インスタンスが 1 つでも欠落していれば無条件 FAIL (R20)。旧実装は実出力のみを走査していたため、期待子音が全欠落しても走査対象自体が空になり検出不能だった。既存 4 音源(voice_a〜d)が新ロジックでも pass することを確認、/k/ 全欠落注入で FAIL になることをテストで確認 | **検証済み** |
| `singer/genesis_v0.py::linkability_audit()`（`genesis_v1.py`/`genesis_v2.py` は独自実装を持たず本関数を再利用） | 候補 embedding の最近傍距離（standin-gallery + voice_A/B/C/D） | 候補ベクトル・参照ベクトル・算出距離のいずれかが非有限なら即座に監査不能（`measurement_valid=False`、`passed=False`、`margin=-inf`）として拒否 (R21)。旧実装は `if d < best:` の単純更新のみで、NaN 距離では `best` が初期値 `inf` のまま残り「無限マージンで最も新規」という fail-open を起こしていた。`run_genesis()` の淘汰理由も "linkability_fail" と区別して "measurement_invalid" を記録するよう追加。同型掃討: `genesis_v1.py`/`genesis_v2.py` は独自実装なし（`gv.linkability_audit` を直接呼ぶ）、`identity_metrics.py::measure_separation()` の `within_e1_max = max(a, b)`（Python 組込み `max()` は NaN が第 2 引数だと無視される順序依存バグ）は `np.max()` へ変更（現状呼び出し元なしの診断専用関数だが同型のため予防的に修正）、`genesis_v0.py::nn_distances()` の `min(dists)` も `np.min()` へ変更（`linkability_audit()` 強化により到達不能になったが多層防御として維持） | **検証済み** |
| `singer/gate_checks.py::_grip_axis()`（`gate6_grip_quick_check()` 経由）/ `singer/gate_checks_v2.py::_grip_axis_v2()`（`gate6_grip_quick_check_v2()` 経由。v1/v2 は別実装） | gate6 grip 判定の sweep×probe 特徴量グリッド | 全特徴 × 全 sweep 点 × 全 probe セルの有限性を PASS の前提条件に追加し、`non_finite_cells` に該当セルを列挙 (R23)。旧実装は `E[f] = float(np.nanmedian(E_note))` が非有限セルを黙って中央値計算から除外するため、1 probe が完全に測定不能でも他 probe が同一値なら中央値が変わらず「不完全な証拠での PASS」を検出できなかった。v1/v2 双方に同型修正を適用。既存 4 音源(voice_a〜d)の gate6 が両版とも非有限セル 0 件・従来どおりの pass/fail 判定のまま非退行することを実測確認、疑似セル注入（レンダリングを monkeypatch で置換した軽量テスト）で 1 probe 完全欠測時に FAIL することを確認 | **検証済み** |
| `proto1/render_health.py::formant_sweep_report()` | P6 formant sweep の formant_scale 掃引セントロイド系列 | 全 formant_scale 点のセントロイドの有限性を PASS の前提条件に追加し、`non_finite_scales` に該当 scale を列挙 (R31)。旧実装は隣接差分を `1.0 if d >= 0.0 else 0.0` で二値化していたため、centroid 測定が NaN になった点を「方向が逆だった 1 ステップ」として黙って 0 加算し、残りの点が健全なら direction_consistency が閾値を超えて測定不能なまま PASS し得た（gate6 grip の R23 と同型）。既存正本の render-health 評価（`sampler.sample` 由来 genome）が非退行で pass することを実測確認、中央 1 点 NaN 注入（レンダリング/特徴抽出を monkeypatch で置換した軽量テスト）で FAIL になることを確認 | **検証済み** |
| `singer/genesis_v0.py::quick_s5()`（`genesis_v1.py`/`genesis_v2.py` は独自実装を持たず本関数を再利用） | genesis 候補の quick-S5 F0 追従（phrase0 先頭 `QUICK_N_NOTES`=4 音） | 4 音全てが有限に測定できた上で全て閾値内であることを `f0_pass` の前提条件に追加 (R32)。旧実装は非有限を除外済みリストの非空判定 (`bool(errs)`) のみで判定していたため、4 音のうち 1 音が未測定（NaN）でもその 1 音がこっそり除外リストから抜け落ち、残り 3 音が健全なら「4 音全て測って良好だった」と取り違えて PASS し得た（render_health.py の R31・gate6 grip の R23 と同型）。1 音欠測注入（軽量モックテスト）で FAIL になること・4 音全て有限かつ閾値内なら従来どおり PASS することの両方をテストで確認。さらに `len(raw_errs) == QUICK_N_NOTES` を `f0_pass` の前提条件に追加 (R36)。R32 の有限性チェックは「取れた音は全部有限か」しか見ておらず「そもそも 4 音取れたか」を検証していなかったため、phrase0 のノート数が退行で 3 音しか無い場合でも `phrase0_rows`/`raw_errs` が静かに 3 要素になるだけで例外にならず、その 3 音が全て有限かつ閾値内なら「4 音全て測って良好だった」と取り違えて PASS し得た（R32 よりさらに手前の、1 音が測定すらされていない不完全証拠 PASS）。3 音のみ返る退行を注入して FAIL になること・4 音（および `[:QUICK_N_NOTES]` で切り詰められる 5 音）なら従来どおり PASS することをテストで確認 | **検証済み** |
| `proto1/plausibility.py::plausibility_report()` | probe（sustain/phrase/cross_range）ごとの周期性 r_median | `zip(result.notes_midi, result.waveforms)` の前に `len(result.waveforms) != len(result.notes_midi)` を照合し、probe ごとの実測 waveform 数が ProbeSpec 由来の期待数と一致しない場合は評価前に測定不能とし、その probe のノートを一切評価に加えず `probe_count_mismatches` に記録して `passed=False` にする (R38)。旧実装は `zip()` が長さの食い違いを例外にせず短い方へ黙って切り詰めるため、レンダラが一部の音を欠落させて返しても検出できず「一部の音だけ測って良好だった」ことを「全音を測って良好だった」と取り違える不完全証拠 PASS になり得た（genesis_v0.py::quick_s5() の R36 と同型）。1 本欠落・全欠落の注入で当該 probe が測定不能 FAIL になること、他 probe は通常どおり評価されることをテストで確認。実際の `probes.render_probe()`（notes_midi と waveforms が常に同数を保証する構築）では `probe_count_mismatches` が常に空であることを非退行テストで確認し、`results_final/genome_registry.jsonl` の全登録 genome に対しても実測で空であることを確認済み（正本の判定結果に影響なし） | **検証済み** |
| `proto1/render_health.py::register_transition_report()` | register_sweep probe（46 音）のフレーム RMS 隣接差 | 実測 waveform 数を `probes.PROBE_DEFINITIONS[probe_name].notes_midi` の期待数（46）と評価前に厳密照合し、不一致なら測定不能 FAIL（`note_rms_db`/`adjacent_db_jumps` は空、`max_db_jump=nan`）とする (R38)。旧実装はレンダラが 0 本または 1 本しか返さない退行が起きても例外にならず、`adjacent_db_jumps` が空リストになるだけで `max_jump = 0.0`（`<=` 閾値内）により「遷移を 1 つも比較していない」のに「全遷移が連続していた」と取り違えて無条件 PASS してしまっていた（plausibility.py の R38 zip 黙落と同型の不完全証拠 PASS）。0 本・1 本の注入でいずれも測定不能 FAIL になること、期待数どおり 46 本なら従来どおり評価されることをテストで確認 | **検証済み** |

**終端宣言**: 上記 19 行のうち、ロード経路（proto1 の 3 経路）・公開経路
（proto1 の 3 経路）・検出ロジック経路（proto1/singer の 7 経路）の計 13
経路は全て検証済み。harness/ の 3 経路（読み込み 2 + 書き込み 1、`harness/
*.py` の各 `main()` は複数ファイルをまとめた 1 行）は凍結・無改変原則に
より明示的に対象外。write-only で対応する loader が存在しない経路
（reference_set の sidecar・一回限りのレポート生成スクリプト・singer/ の
JSON 書き出し。計 3 経路）も明示的に対象外（3+3=6 経路）。**全行が「検証
済み」または明示的な境界宣言に分類されたため、C1–C6/R1–R11/R12–R13/
R14–R16/R17–R19/R20–R21/R22–R23/R24–R25/R26/R27–R28/R29–R30/R31–R33/
R34–R36/R37–R38 の 16 ラウンドにわたる「ローダー/来歴/公開/検出ロジック」系
残穴の指摘サイクルをここで終端とする。** 今後同種の指摘が来た場合は、本表に新しい行を
追加できるかどうか（＝これまで見落としていた経路か）をまず確認すること。
既存行の再指摘であれば、当該行の「検証内容」列に記載済みの対処で十分か、対処
自体に不備があるかを個別に判断する。

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
