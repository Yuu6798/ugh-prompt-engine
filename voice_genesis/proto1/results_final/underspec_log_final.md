# underspec_log_final.md — 最終統合サイクル（F1〜F3）で補充した判断

`final_assembly_memo.md` が決めきれていない箇所の実装判断を記録する。
`underspec_log_p1.md` の `[UNDERSPEC-P1-n]` に続き、本ログは
`[UNDERSPEC-F-n]` で通し番号を振る。

## [UNDERSPEC-F-1] proto1_demo.py: mutate の scale パラメータ

`final_assembly_memo.md` F1-1 は `mutate(101系, seed=303)` とだけ書き、
`scale` を指定していない。判断: `sampler.mutate()` の呼び出しで
proto1_design_memo.md 側のテスト・デモ群が繰り返し使ってきた代表値
`scale=0.1`（`MUTATE_SCALE` 定数）を踏襲した。「小さな変異」を実演する
には十分な大きさで、かつ物理事前分布を外れて `out_of_physio_range` に
なる確率が低い値として一貫して使われてきた実績を優先した。

## [UNDERSPEC-F-2] reference_set.py: linkability report_id を created_at 非依存に変更

F1-6「デモ全体を 2 回実行し genome_id・波形 hash・監査判定が一致すること
（created_at のみ差異許容）を機械照合する」を実装する過程で、既存の
`audit_linkability()`（P1 で実装済み）の `report_id` が
`{genome_id, reference_set_hash, created_at}` の hash から作られており、
2 回の実行で real wall-clock の `created_at` が異なる限り `report_id` も
毎回異なってしまうことが判明した。`report_id` は Genome の
`audit.linkability_report_id` に書き戻され、Genome の content hash
（`genome_id`）の計算対象に含まれるため、そのままでは「genome_id が
2 回の実行で一致する」という F1-6 の要求を原理的に満たせない
（`created_at` が genome_id に間接的に伝播してしまう）。

判断: `report_id` の計算から `created_at` を除外し、
`{genome_id, reference_set_hash}` のみの内容アドレスに変更した
（`reference_set.py` の `audit_linkability()`）。`created_at` は
「いつ実行されたか」の記録用フィールドとして `LinkabilityAuditReport.created_at`
に引き続き残るが、`report_id` 自体の同一性判定には関与しない。これにより
「同一候補 × 同一 reference set の監査は常に同一 report_id になる」という、
そもそも「版管理された手続き」の趣旨に合致する性質を得られた
（wall-clock に依存しない内容アドレスの方が本来の設計として正しいという
判断。P1 実装時点ではこの整合性問題が顕在化していなかった）。

この変更は P1 の `tests/test_reference_set.py` の既存アサーションと矛盾
しないことを確認済み（`report_id` の具体的な hash 入力を検査するテストは
元々存在せず、ペア関係 `report.reference_set_hash == gallery.sha256` と
決定論性 `audit_linkability` を同一引数で 2 回呼んでも同じ結果になること
のみを検査していたため、全て pass のまま）。

## [UNDERSPEC-F-3] registry.py: eval フィールドの型を `Dict[str, Optional[float]]` から `Dict[str, Any]` へ緩和

P1 では `eval.{plausibility,grip_ref,novelty}` は常に `None` プレースホルダ
だったため型ヒントを `Dict[str, Optional[float]]` としていたが
（`underspec_log_p1.md` [UNDERSPEC-P1-8]）、F1 では実測の構造化オブジェクト
（例: `grip_ref = {"ref": "...", "gate_semantics_version": "..."}`）を渡す
必要があるため `Dict[str, Any]` に緩めた。ランタイム動作に影響はない
（Python の型ヒントは実行時に強制されない）。既存テストへの影響なし。

## [UNDERSPEC-F-4] proto1_demo.py: grip_ref を「参照のみ」で持たせる具体的な粒度

`final_assembly_memo.md` は「grip はエンジン×軸の性質（Genome 個体の性質
ではない）ため...値のコピーではなく参照」と方針を述べるのみで、
registry エントリの `eval.grip_ref` に何を書くかの正確なスキーマは
与えていない。判断: `{"ref": "vt_harness/results_v6/grip_report_v6.json",
"gate_semantics_version": "grip-v2/band-v4/frozen-v6"}` の 2 フィールドの
みとし、grip の実測数値（grip_ratio・E_intended 等）は一切埋め込まない
（数値を要約としてでも埋め込むと「参照」ではなく実質的な「値のコピー」に
なってしまうため、ファイルパス参照とゲート意味論バージョン文字列のみに
厳格に限定した）。実測値そのものは `results_final/acceptance_report.md`
側で `vt_harness/results_v6/` を直接引用する形にした（読み取りのみ）。

## [UNDERSPEC-F-5] proto1_demo.py: plausibility ゲートの判定粒度（note 単位、VT-1 に整合）

`final_assembly_memo.md` F1-3 は「sustain/phrase/cross_range の各レンダで
periodicity r_median >= 0.35」とのみ書き、probe 単位の集約値で判定するか
ノート単位で判定するかを明記していない。判断: 比較対象として明示的に
名指しされている vt_harness VT-1（`results_v3/vt1_plausibility_v3.json`,
`r_threshold=0.35`）がノート単位（122 ノード中の違反数）で判定している
ことに合わせ、本デモも 3 probe（sustain 3 音 + phrase 8 音 + cross_range
2 音 = 13 ノート）の**個々のノート**に閾値を課すノート単位判定を採用した
（`plausibility.py` を新設。1 ノードでも違反すれば genome 全体を
plausibility 不合格とする、VT-1 と同型の厳格さ）。

## [UNDERSPEC-F-6] proto1_demo.py: 監査 PASS 個体の選出方法

F1-7「監査 PASS した個体 1 つ」をどう選ぶかは規定されていない。判断:
4 Genome を生成順（a=sample101, b=sample202, c=mutate303, d=crossover404）
に走査し、`linkability_audit.overall_pass=True` になった最初の個体を採用
した（決定論的なタイブレークルール。恣意的な「最良」選定基準を導入せず、
生成順という既に決定論的な順序をそのまま使う）。

## [UNDERSPEC-F-7] proto1_demo.py: cross_range ペアの WAV 形式

F1-7「cross_range ペアの WAV も保存」がステレオ 2ch か、2 つの別ファイルか、
1 つの連結モノラルファイルかを規定していない。判断: phrase probe の WAV
と同じ「1 つのモノラル WAV」形式に揃え、C3 ノート → 0.15 秒の無音 →
C6 ノートの順に連結した単一ファイルとした（`prototype1_cross_range_pair_
<genome_id>.wav`）。無音ギャップは 2 音が地続きで鳴って聴感上つながって
しまうのを避けるための最小限の区切りで、値自体に音響的意味はない。

## [UNDERSPEC-F-8] proto1_demo.py: 決定論検証（F1-6）の比較スコープ

「genome_id・全波形 hash・監査判定が一致すること（created_at のみ差異
許容）」を機械的に実施するにあたり、比較対象から除外するフィールドを
`created_at`（文字通り）のみとした。ただし `run_pipeline()` の戻り値に
含まれる `registry_path`（呼び出しごとに異なる一時ファイルパス文字列）は
実行環境依存の非本質的な差異であり、比較前に明示的に pop して除外した
（[UNDERSPEC-F-2] の `report_id` 修正により、この 2 フィールドを除けば
残り全体が真に決定論的に一致するようになった）。差異が検出された場合に
備え、最初に見つかった相違点を最大 50 件までリストする `_diff_paths()` を
実装し、`e2e_run.json.determinism_check.differing_paths` に記録する構造
にした（今回の実測では空リスト = 完全一致）。

## [UNDERSPEC-F-9] acceptance_report.md: formant_scale 記載の数値の出典差異

`final_assembly_memo.md` F2 の正直会計セクションの原文は
「効果は実証済み（E=5.05、免除表の数値条件全成立）だが方向一致率
0.60 < 0.90...」と記述する。コーディネーターの指示は「formant_scale
open issue の記載内容はメモの文言に従う」であり、本文言を acceptance_report
にそのまま転記した。ただし `vt_harness/results_v6/grip_report_v6.json`
（本サイクルの一次 evidence）を直接確認したところ、v6 確定値は
`E_intended=grip_declared=4.3537`（`E=5.05` ではなく、v5 サイクルの
免除後値 `5.051` に近い）であり、`E=5.05` は v6 ではなく v5 時点の数値と
一致する。**メモの文言（E=5.05）はそのまま転記しつつ**、直後に
`vt_harness/results_v6/grip_report_v6.json` から直接読み取った v6 確定値
（`grip_declared=4.3537, sign=5/5, E_declared=1.7616<=2.1768, dir=0.60`）
を脚注として併記し、**総合判定はメモの丸めた文言ではなく v6 の一次
evidence の実測値に基づいて行う**旨を明記した（F2 の「判定は evidence の
指す実測値のみに基づくこと」という指示と、「メモの文言に従う」という指示
の両方を、前者を数値の根拠、後者を文章の体裁として両立させる解釈を採った）。

## [UNDERSPEC-F-10] proto1_demo.py: 2 回実行のうち 1 回目の registry を破棄する設計

F1-6 の決定論検証は 2 回の完全実行を要求するが、両方の registry を
`results_final/` に永続化すると重複した成果物になり、かつ append-only
ストアの性質上どちらが「正本」か曖昧になる。判断: 1 回目は一時ファイル
（`results_final/_scratch_registry_run1.jsonl`）に書き、決定論比較の後に
削除する。2 回目を `results_final/genome_registry.jsonl` として正本の
成果物に採用した（内容は 1 回目と `created_at` を除き完全に同一であること
を機械照合済みのため、2 回目のみを残しても情報の欠落はない）。
