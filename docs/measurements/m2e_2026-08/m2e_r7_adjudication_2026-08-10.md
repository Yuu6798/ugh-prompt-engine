# M2e r7 blocker 設計裁定 + evaluate 計画予算承認 — 2026-08-10

- **裁定日**: 2026-08-10
- **裁定者**: Fable 5 設計セッション（本 memo を含む PR）
- **対象**: (1) r7 step0 fail-closed 停止
  （`m2e_r7_blocker_stem_sha256_2026-08-09.md`）への裁定 4 点、
  (2) r7 evaluate 実行計画（`m2e_r7_evaluate_plan_2026-08-09.md` §6）の予算承認
- **結論**: **裁定 4 点は全て承認（(a)(b)(c) は PR #254 実装の事後追認）。
  予算値を承認。r7 は再開可能——律速は設計裁定から機械時間（≈119h）へ戻った。**

---

## 0. 経緯の正確化（一次ソース照合の結果）

本裁定の起草時点で、事実関係は blocker 記録・STATUS.md・セッションメモリの
いずれとも異なっていた。git 履歴の照合で確定した経緯:

| 時刻 (UTC) | 事象 | 一次ソース |
|---|---|---|
| 2026-08-09 00:25 | r7 step0 が fail-closed 停止（store_A 無傷・store_B 未作成） | blocker 記録 commit `bed1066` |
| 2026-08-09 03:40 | 検証レイヤ修正を実装（allowlist 分離 + 束 digest） | commit `1dbf966` |
| 2026-08-09 同日 | 署名復帰（run 間決定論証拠として stem/bundle を保持） | commit `42378bb` |
| 2026-08-09 同日 | r6 実測 1280 セルの resume 保全（generator code 等価表 + 機械的失効機構） | `generator_code_equivalence_2026-08-09.md`（裁定者: Fable 5 設計セッション `session_01XBrHyRfRBAGgS9gGHtiKdg`） |
| 2026-08-09 13:08 | **PR #254 マージ（Codex レビュー済み）** — 上記全てが main 入り | merge commit `74d7289` |
| 2026-08-10 | PR #256 セッションが blocker 記録（未更新のまま）を一次ソースとして「裁定待ち」と判定・handoff 化 | `.claude/memory/2026-08-10.md` |

つまり**実体裁定と実装は 08-09 の設計セッションが完了させていた**が、blocker
記録・r7 計画 doc・STATUS.md への反映（dated 裁定記録）が欠落したため、後続
セッションが stale な「裁定待ち」を再生産した。本 memo は (i) 4 点の事後追認を
検証付きで正式化し、(ii) 欠落していた dated 記録を補い、(iii) 残っていた唯一の
実質未裁定＝予算承認を出す。

なお blocker 記録が引用する commit `f06bbaa3`（停止報告）・`dada954d`（r7 計画）は
本リポジトリ履歴に存在しない（PR #254 ブランチの rebase で `bed1066`・`1e3dbc6` へ
書き換わったと判断）。以後の参照は後者を使う。

## 1. 裁定 (a) — 検査意図の縮小: **承認（追認）**

L5181 系カテゴリ集約検査の意図「分離器スタックの run 間同一性」は、
**カテゴリ不変量（`preprocessing` / `separation_model` / `separation_version` /
`separation_weights_sha256` / `separation_code_sha256` / `separation_code_packages`）の
完全同一 + `provenance_preprocessing` 有無フラグの同一**へ正確化する。
`stem_sha256` は分離出力そのものの指紋であり per-mix（clip×bed）固有＝
カテゴリ不変量として扱っていたことが検査側の誤りで、測定側は正しい
（blocker 記録の機械集計: 80/80 セルで相異は `stem_sha256` のみ・分離器 pin は
全セル同一、`separation_weights_sha256` は凍結値 bf1218da… と一致）。

実装照合（`scripts/run_melody_accuracy.py`）: `PER_CLIP_PREPROCESSING_KEYS =
frozenset({"stem_sha256"})` の **allowlist 方式**（未知キーは不変量側＝安全側へ
倒す）+ `split_preprocessing_invariants()`。有無フラグを比較対象へ明示的に含め、
None と `{}` の潰れも塞いでいる。**fail-closed の緩和ではなく検査意図の正確化**で
あり、禁止事項（検査の削除・store_A への変更）に抵触しない。

## 2. 裁定 (b) — category row への載せ方: **束 digest を承認（追認）・代表値は不採用**

実行側見立てどおり、category row には
**「(clip_id, stem_sha256) 対を clip_id で sort した束の sha256」**
（`stem_sha256_bundle`）を載せる。clip 識別子を対に含めることで「どの stem を
どの clip の分離出力として測ったか」の帰属を捨てない（D-2）。代表値
（clip_rows[0] の stem）は帰属未定義のため不採用——blocker 記録の指摘どおり。
stem を持つ clip が 1 件もなければ bundle を出さず、V_direct 等 preprocessing
なし経路の report 形は不変。

## 3. 裁定 (c) — 同型掃討範囲: **3 検査 + 実行証拠突合の 4 箇所で終端（追認）**

blocker 記録「影響範囲」の 3 箇所すべてが `split_preprocessing_invariants()` へ
統一されていることを実装照合で確認:

1. **`_run_external_category` カテゴリ集約**（停止点）: §1 のとおり不変量のみ同一要求。
2. **`_row_model_stack_signature`**: per-row の `stem_sha256`（S_fullstack 等の
   1 row = 1 clip 行）と `stem_sha256_bundle`（集約行）を**署名に保持**する
   （`42378bb`）。ここで比較される rows は「同一測定の run 間比較（repeats /
   submitted vs 検証 run）」であり、同じ clip を同じ分離器で分離し直した stem
   bytes は決定論で一致すべき——カテゴリ内 clip 間比較（stem 除外が正しい）とは
   文脈が異なる。metrics 一致だけでは stem bytes の非決定性が量子化で消えて偽の
   決定論 success を publish しうるため、除外ではなく保持が正しい。
3. **`_require_homogeneous_model_stack`**: fullstack 分岐（per-clip 行に
   `preprocessing.stem_sha256` の真 sha256 を要求）は kind="fullstack" のみに適用。
   `V_remix_real_stem` は kind="external" のため集約行（stem は bundle 側）と
   衝突しない——照合済み。
4. **実行証拠との突合**（evaluate 側）: 不変量側だけで行う（stem_sha256 は
   評価環境から再計算不能な per-clip 量のため対象外が正しい）。

および r6 実測との継続性: 検証レイヤ変更で `_generator_code_sha256()` 閉包 hash が
動くため、store_A（r6・1280 セル・84.5h）の resume は generator code **等価表 +
attestation 後継束縛（機械的失効機構）**で保全済み
（`generator_code_equivalence_2026-08-09.md`・PR #254 で Codex レビュー済み）。

## 4. 裁定 (d) — 事前登録整合: **抵触なしと判定・dated 追補は本 memo が充足**

- 変更されたのは **M2c 期（commit 61876c85）由来のハーネス検証コードのみ**。
  M2e の事前登録凍結物——store_A・`m2e_vremix_fixtures_*.yaml`・bars 登録・
  ミックス生成仕様（DESIGN §4）・step0 の HANDOFF §5 逐語コマンド——は
  1 バイトも変わっていない。複数 clip × per-clip stem のカテゴリがこの集約検査を
  通るのは M2e が初であり（r6 shard 実行機は run report を出さないため非発火）、
  凍結後に顕在化した**検査側の潜在欠陥の是正**であって、測定手続き・判定規則の
  事後変更ではない。
- ただし「事後変更なしの原則」に対する透明化として dated 裁定記録が必要
  ——それが欠落していた（§0）。**本 memo がその追補**である。r6 セル継続性の
  技術的記録は `generator_code_equivalence_2026-08-09.md` が既に担っている。
- **ガバナンス逸脱の正直記録**: 設計裁定（08-09 セッション）が実装と同時に
  行われ、blocker 記録が要求した「設計側判読を待つ」の *記録* が事後になった。
  実体判断は正しかったが、記録欠落が 08-10 の stale handoff（「裁定待ち」再生産）
  を生んだ。再発防止は AGENTS.md §8「正典台帳への起草は一次ソース照合必須」
  （本 PR で編入）+ 裁定は必ず dated memo を同一 PR に含める運用とする。

## 5. r7 evaluate 計画の予算承認（plan §6 への回答）

`m2e_r7_evaluate_plan_2026-08-09.md` の予算値を**そのまま承認**する:

- **採用単価 335 s/cell**（r6 定常実測 291.8 の +15% ≒ r6 最悪 shard。校正基準
  ×3 安全率 250.5 より保守側——採用理由も妥当）
- **見積り: 1 水準 320 セル ≈29.8h・全 4 水準 1280 セル ≈119h**
- **チャンク区切り B_session=7200s + hang 上限 600s**・同一 store_B 再開
- **run 回数上限（plan §3 の「値は設計側裁定に従う」への裁定）: 水準あたり 18・
  全体 72 で確定**。超過時は停止・設計側へ報告
- **単価監視: >500 s/cell が 2 チャンク連続で停止・報告**（新ドリフト検出）
- **報告規律（plan §5）を厳守**: 帯の判定・水準別数値は census(C5) のみ。
  evaluate 進行中の報告は完了セル数・逸脱・単価実測のみ

## 6. 実行側への再開指示

1. main を pull し、PR #254 込みの checkout で **step0 を再実行**
   （`build/m2e/run_r7_step0_reports.sh`・HANDOFF §5 逐語形のまま変更不要。
   store_A から 100% resume・`generator_code_predecessors` が report に載ることは
   等価表受理の正常な痕跡）。
2. step0 の run report が 4 水準そろい次第、plan §1 の evaluate を
   **p12 → p06 → p00 → m06** の順でチャンク実行（store_B は空から・4 水準共用）。
   **チャンクの時間区切り（plan §3 の B_session=7200s + hang 上限 600s）は、
   checkout 非依存の逐語形として以下で実施する**（2026-08-11 追記・PR #257
   レビュー指摘の採用。plan §1 の evaluate CLI 単体は timeout を持たず、
   `--session-budget` は shard 実行機のみのため、素の §1 形では 1 水準
   ≈29.8h を一括で走らせてしまい 18/72 run 会計・単価ドリフト停止条件が
   機能しない）:

   ```bash
   # そのまま実行可能な形（HANDOFF §5 の canonical 作業成果物パスに束縛。
   # プレースホルダ禁止 = HANDOFF E-132 と同型の是正・PR #257 第 4 指摘）。
   # run report は step0 が HANDOFF §5 形で build/m2e/run_<水準>_r{0,1}.json へ
   # 出力済みであることが前提（未生成なら本ループは fail-closed で止まる）。
   # run 会計・単価記録はシェル再起動を跨いで永続する（1 run = 1 marker ファイル・
   # チャンクログ追記式 = PR #257 第 5 指摘の採用。plan §4 の「>500 s/cell 2 チャンク
   # 連続で停止」もログに対する機械判定として内蔵）。
   state_dir="build/m2e/r7_chunk_state"; mkdir -p "$state_dir"
   for lvl in p12 p06 p00 m06; do
     while :; do
       # 完走済み水準は skip（再起動時に完走水準を再走すると cells_delta=0 の
       # 成功チャンクが slow 判定され、未完走水準へ到達する前に誤停止する =
       # PR #257 第 7 指摘の採用）。
       if [ -e "$state_dir/level_${lvl}.complete" ]; then break; fi
       # run 計上は**起動前**の .started マーカーで行う（timeout 終了後の記録だと
       # ホスト死・端末切断のチャンクが過少計上され 18/72 を超えうる = 第 8 指摘）。
       runs=$(find "$state_dir" -name "run_${lvl}_*.started" | wc -l)
       if [ "$runs" -ge 18 ]; then
         echo "r7 evaluate run cap reached: lvl=${lvl} (18/水準・全体 72)" >&2; exit 1
       fi
       stamp=$(date -u +%Y%m%dT%H%M%SZ)
       : > "$state_dir/run_${lvl}_${stamp}.started"
       cells_before=$(find build/m2e/store_B -type f 2>/dev/null | wc -l)
       start_utc=$(date -u +%s)
       status=0
       timeout --kill-after=600 7200 \
         env OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/run_melody_accuracy.py \
           --out "docs/measurements/m2e_2026-08/verdict_${lvl}.json" \
           --evaluate "build/m2e/run_${lvl}_r0.json" "build/m2e/run_${lvl}_r1.json" \
           --external-manifest "build/m2e/manifest_${lvl}.json" \
           --external-fixtures "tests/fixtures/melody_bench/m2e_vremix_fixtures_${lvl}.yaml" \
           --eval-cell-store build/m2e/store_B --workers 2 --pin-threads || status=$?
       elapsed=$(( $(date -u +%s) - start_utc ))
       cells_delta=$(( $(find build/m2e/store_B -type f 2>/dev/null | wc -l) - cells_before ))
       printf '%s lvl=%s exit=%s elapsed=%s cells_delta=%s\n' \
         "$stamp" "$lvl" "$status" "$elapsed" "$cells_delta" >> "$state_dir/chunk_log.txt"
       # exit 124 = timeout によるチャンク打ち切りの正常系。0/124 以外は fail-closed 停止。
       if [ "$status" -ne 0 ] && [ "$status" -ne 124 ]; then
         echo "r7 evaluate fail-closed: lvl=${lvl} exit=${status}" >&2; exit "$status"
       fi
       # 成功時は単価判定より先に完走マーカーを永続化する（単価停止で exit しても
       # 再起動時にこの水準を再走しない。verdict は emit 済み）。
       if [ "$status" -eq 0 ]; then : > "$state_dir/level_${lvl}.complete"; fi
       # 単価監視（plan §4）: 直近 2 チャンクがともに >500 s/cell（cells_delta<=0 含む）
       # なら新ドリフトとして停止・設計側へ報告。**成功チャンク（status=0）にも
       # 適用してから break する**——水準の最終チャンクが 2 連続超過の 2 本目である
       # 場合、次水準へ進む前に停止・報告するのが plan §4 の趣旨（PR #257 第 6 指摘）。
       rate=$(tail -n 2 "$state_dir/chunk_log.txt" | awk '
         { split($4, e, "="); split($5, c, "=");
           if (c[2] <= 0 || e[2] / c[2] > 500) n++ }
         END { print (NR == 2 && n == 2) ? "stop" : "go" }')
       if [ "$rate" = "stop" ]; then
         echo "r7 evaluate: >500 s/cell が 2 チャンク連続 — 停止・設計側へ報告" >&2; exit 1
       fi
       if [ "$status" -eq 0 ]; then break; fi
     done
   done
   ```

   同一 `--eval-cell-store build/m2e/store_B` の再指定で次チャンクが resume する
   （セルレコードは atomic write のため打ち切りで部分レコードは残らず、
   verdict JSON は完走時のみ emit される＝打ち切りは安全）。
   実行側に既存の `build/m2e/run_r7_evaluate.sh` を使う場合は、**起動前にその
   bytes を dated record として commit し sha256 を初回進捗記録へ載せる**
   （untracked runner のまま run 会計・停止条件を運用しない）。
3. 監視・停止条件は plan §4 のとおり（swap so>0 即報告・fail-closed 即停止）。
4. verdict JSON は `docs/measurements/m2e_2026-08/verdict_<lvl>.json` へ commit。
   判読は設計側（帯判定は census(C5) のみ）。

## 7. 本裁定で更新した stale 記述（同語横断掃討）

「裁定待ち / 停止中」を現在の正典主張として残す箇所を全数更新（履歴・監査証跡は
改変せず dated 追記のみ）:

- `m2e_r7_blocker_stem_sha256_2026-08-09.md` — 末尾に現況追記（解消済み・本 memo 参照)
- `m2e_r7_evaluate_plan_2026-08-09.md` — 冒頭 status に承認追記
- `.claude/memory/STATUS.md` — Phase / queue「M2e r7 → 破断曲線判読」行を現況へ
- `docs/intent/graph.yaml` — M2e ノードの note・evidence を現況へ（PR レビュー経由）
- `.claude/memory/_index.md` — 08-10 行へ superseded 注記（起動時必読のため。
  PR #257 レビュー指摘で補完）
- `.claude/memory/2026-08-10.md` — Handoff 末尾へ dated 追記（Task 1 前提の
  supersession。本文は起草時点の記録として保持。同上）
