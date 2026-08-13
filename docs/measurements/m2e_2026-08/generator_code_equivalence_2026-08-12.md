# Generator Code Hash 等価表 Attestation — 前任 `5cc0d5f9…` / `2b234e2b…`（2026-08-12）

**裁定日:** 2026-08-12
**裁定者:** Fable 5 設計セッション（`session_01XBrHyRfRBAGgS9gGHtiKdg`）
**参照:** M2e r7 evaluate main の m06（-6dB）水準開始直後の fail-closed 停止
（`run_melody_accuracy.py: error: argument --level: expected one argument`）。
`evaluate_m2_bars` の測り直し子プロセス起動（`_run_external_verification_in_fresh_process`）
が `--level` を分離形 argv（`["--level", "-6dB"]`）で渡すため、先頭ダッシュの水準値が
argparse にオプションフラグと誤解釈される——`+12dB` / `+6dB` / `0dB` では顕在化せず、
m06 で初めて踏む引数パース失敗。本文書は、その等価形修正で閉包 hash が
`2b234e2b…` → `b3bf304c…` へ動くことに伴う等価表の再裁定である。

前回 attestation = [`generator_code_equivalence_2026-08-09.md`](generator_code_equivalence_2026-08-09.md)
（以下「08-09 文書」）。同文書 §7 の運用規則に従い、**08-09 文書は歴史記録として
凍結維持し編集しない**。前任 `5cc0d5f9…` の新後継への束縛し直しは本文書が担う。

---

## 1. 等価表エントリ（本裁定後の全量）

```python
GENERATOR_CODE_EQUIVALENT_SHA256S: Dict[str, str] = {
    "5cc0d5f9bba92ce8aa679eeebc32845e7702b6ac8e2bb1f561ba37c37ab965a4": (
        "docs/measurements/m2e_2026-08/generator_code_equivalence_2026-08-12.md"
    ),
    "2b234e2be2fcc590daed82038c691643c8c995954934204d8a8562cd31835088": (
        "docs/measurements/m2e_2026-08/generator_code_equivalence_2026-08-12.md"
    ),
}
```

両エントリとも本文書を指す（`_parse_attested_successor_sha256` は文書中の最初の
有効な `attested_successor_sha256:` 行を読むため、後継宣言は §2 の 1 行に一元化される）。

- **`5cc0d5f9…`** = commit `32288aa8` 時点の閉包 hash。M2e r6 帯（`store_A`・
  1280 セル・84.5h）はこの checkout で測定された。出所検証は 08-09 文書 §5–6
  （worktree 実行による実測再現）が正であり、本文書はそれを参照により編入する。
- **`2b234e2b…`** = PR #254 マージ後の `main`（本裁定時点の `origin/main` =
  `f391591`）の閉包 hash。M2e r7 evaluate の `store_B` 既測 960 セル
  （p12/p06/p00 の 3 水準）と 3 verdict はこの checkout で測定された。
  この値は 08-09 文書 §2 の `attested_successor_sha256` と同一（= 前回裁定の
  後継が今回の前任になる、という系譜の素直な継承）。

## 2. 後継 digest 束縛（機械的失効機構）

受理条件の二重化（08-09 文書 §2 と同一機構）: エントリのキー一致に加えて、
本文書が宣言する以下の値が現在の `_LOADED_GENERATOR_CODE_SHA256` と一致すること。

```
attested_successor_sha256: b3bf304cc21a0b2a7c426d418a7b8a078896e116e5cb00e40fa8a111fb3c1c9c
```

この値は本裁定のコード変更（§3 の 2 編集）を全て確定させた後の checkout 上で
`_generator_code_sha256()` を直接実行して得た実測値である。本文書自体は 69 ファイル
閉包の外にあるため、この行の記載は閉包 hash を変えない（自己参照は生じない）。

## 3. 後継差分（`2b234e2b…` → `b3bf304c…`）の全量と非該当理由

差分は `scripts/run_melody_accuracy.py` の **2 編集のみ**（他の閉包 68 ファイルは
bytes 不変）:

| 編集 | 内容 | per-cell 測定経路への該当性 |
|---|---|---|
| argv 等価形修正 | `_run_external_verification_in_fresh_process` の子プロセス argv 構築を `["--level", level]` → `[f"--level={level}"]` へ変更 | **非該当**。変更は親側の argv 組み立てのみ。argparse の長オプションは `--level=X` と `--level X` を同一に解釈するため、既測 3 水準（`+12dB`/`+6dB`/`0dB`）では子の受け取る `args.level` は 1 bit も変わらない。`-6dB` は旧形式では子が exit 2 で即死しており（セルレコード・report・verdict のいかなる永続化成果物も旧挙動を体現していない）、新形式で初めて測定可能になる。音声読み込み→抽出→metrics 計算→セルレコード書き込みの各ステップは無変更 |
| 等価表更新 | `GENERATOR_CODE_EQUIVALENT_SHA256S` へ `2b234e2b…` エントリ追加 + `5cc0d5f9…` の参照先を本文書へ差し替え | **非該当**。検証レイヤの凍結定数のみ（08-09 文書 §5 の等価表導入コミット群と同種）。測定経路から参照されない |

## 4. 閉包 touch コミットの全数掃討（`32288aa8` → `origin/main` = `f391591`）

`_generator_code_paths()` が返す 69 ファイルの相対パス一覧を pathspec として
`git log --oneline 32288aa8..origin/main -- <69 ファイル>` を実行した結果、
閉包へ触れたコミットは以下の 8 件のみ:

- `8b3f737` — **非該当**（M3d ベンチ生成スクリプトへの後方互換追加。08-09 文書 §5 で裁定済み）
- `1dbf966` / `42378bb` / `0e42d2d` / `56d1175` / `514477a` / `6e4ab80` / `dd147f6`
  — PR #254 のコミット群。**非該当**（カテゴリ集約・検証レイヤのみ。08-09 文書 §4–5 で裁定済み）

**PR #254 マージ（`74d7289`）以後、本裁定の 2 編集より前に閉包へ触れたコミットは
存在しない**。実証: `origin/main`（`f391591`。PR #255–#258 マージ後）での
`_LOADED_GENERATOR_CODE_SHA256` 実測値が `2b234e2b…` のまま 08-09 文書 §2 の宣言値と
一致する（PR #255/#256/#257/#258 は docs / `.claude/memory` / 閉包外の
`src/svp_rpe` 新規モジュールのみで、69 ファイルの bytes を変えていない）。

## 5. 検証方法

- **前任 `2b234e2b…`**: `origin/main`（`f391591`）checkout 上で
  `scripts/run_melody_accuracy.py` を `importlib.util.spec_from_file_location` で
  ロードし `_LOADED_GENERATOR_CODE_SHA256` を読み取り、08-09 文書 §2 の宣言値との
  一致を確認した（2026-08-12 実測）。
- **前任 `5cc0d5f9…`**: 08-09 文書 §6.1（`32288aa8` worktree 実行）を参照により編入。
- **後継 `b3bf304c…`**: §3 の 2 編集を確定させた working tree 上で同手法により実測。
  マージ後の再確認手順は 08-09 文書 §6.4 と同一（マージ後 `main` HEAD で
  `_generator_code_sha256()` を再計算し §2 と照合。他 PR が先に閉包へ触れて
  いれば照合が外れ、機械的失効が働く——意図どおりの fail-closed であり障害ではない）。

## 6. 帯会計への含意（r7 evaluate の続行条件）

本裁定により、後継コード（`b3bf304c…`）の下で:

1. `store_B` 既測 960 セル（`generator_code_sha256 = 2b234e2b…`）は
   `_cell_record_mismatches` の等価受理で resume 可能（再測定不要）。
2. 既生成 3 verdict（p12/p06/p00、generator = `2b234e2b…`）と新規 m06 verdict
   （generator = `b3bf304c…`）は `aggregate_m2e_census`
   （`_require_homogeneous_census_inputs`）の等価受理で合算可能。
3. step0 由来の系譜宣言 `generator_code_predecessors = ["5cc0d5f9…"]` は
   `5cc0d5f9…` エントリ（本文書束縛）の受理可能性検査を通過する。

つまり **r6 の 84.5h（store_A 1280 セル）と r7 既測の 960 セル + 3 verdict は
1 件も失われない**。

## 7. 運用規則

08-09 文書 §7 をそのまま継承する: 測定経路に触れる変更では全エントリ削除 +
再裁定が既定の規律（一次防御）、後継束縛による機械的失効が二次防御。等価表は
「コード変更の免罪符」ではなく、diff スコープを attestation で個別に裁定した例外
である。新しい前任 hash の追加、または既存前任の新後継への束縛し直しは、本文書と
同型の attestation を新規作成し、`GENERATOR_CODE_EQUIVALENT_SHA256S` の参照先を
差し替えること（既存 attestation 文書は編集しない）。
