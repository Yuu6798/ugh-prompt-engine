# 承認ファイル（Gate 1–3）— 配置先ガイド

このディレクトリは **場所の説明専用**（IMPLEMENTATION_MAP_v1.md §6.1）。
承認ファイルの**正本**（`approvals.load_approval()`/`check_armed()` が実際に
読む JSON）は本リポジトリのどこにも一切格納しない — 唯一の正本は checkout
**外** の `VG_CAL_APPROVAL_DIR` に置く（§配置場所）。

`records/` サブディレクトリには、監査・引き継ぎ用途の**参照用コピー**
（正本と同一内容の JSON）と、決定の経緯・根拠を記す Markdown
（例: [`records/GATE1_DECISION_RECORD.md`](records/GATE1_DECISION_RECORD.md)）
を置くことがある。**これらはいずれも loader が読む対象ではない** — 上記の
「正本はリポジトリに格納しない」という原則を破るものではなく、あくまで
「正本に何が書かれていたか」を後から追跡できるようにするための、
バージョン管理された監査証跡である。承認の有効性判定は常に
`VG_CAL_APPROVAL_DIR` 直下の実ファイルのみに基づく。

campaign manifest（`campaigns/*/c0_manifest.json`）の `approvals.gate*_sha256`
に pin された参照コピーは、以後**不変**（再 stamp 禁止）である —
`DESIGN_VG_METER_CAL_DEBT_*.md`/`IMPLEMENTATION_MAP_v1.md` の編集で
working tree 側の実測 sha256 が動いても、そのコピー自体は書き換えない
（書き換えると closed campaign の pin と食い違い、将来の監査を汚染する）。
tree との整合（`refresh_document_hashes()` での追随）が要求されるのは、
まだどの manifest にも pin されていない**未消費**の参照コピーのみ
（`tests/test_approvals.py` の regression guard もこの区別で判定する）。

## 配置場所

承認ファイルは checkout **外** の `VG_CAL_APPROVAL_DIR`（環境変数。既定
`~/.vg_cal/approvals/`）に置く。

理由（IMPLEMENTATION_MAP_v1.md §6.1）:

- checkout 内の未追跡ファイルは dirty-tree 判定（`c0_validate.py` の
  `repo.dirty_tree` REQUIRED_BLOCKING 検査）で C0 freeze の武装経路を自己
  否定する。
- コミットすれば HEAD が変わり、manifest 派生の campaign identity
  （`manifest_core_sha` / `campaign_id`）が動いてしまう。

`VG_CAL_APPROVAL_DIR` を明示的に設定しない場合、`approvals.default_approval_dir()`
は `~/.vg_cal/approvals/` を使う。

## ファイル名

`VG_CAL_APPROVAL_DIR` 直下に、以下 3 ファイルを配置する
（`approvals.APPROVAL_FILENAMES`）:

| Gate | ファイル名 | 解錠する操作 |
|---|---|---|
| Gate 1（campaign 実行承認 + 費用上限 + 最大 claim / E_use 境界） | `gate1_campaign_execution.json` | campaign 実行（D2 runner の armed 実行）+ cost caps 3 値の確定 + E_use 境界の受容 |
| Gate 2（C0 freeze の実行承認） | `gate2_c0_freeze.json` | `c0_freeze.py` の armed 実行（secret 生成・`campaigns/<id>/` への公開） |
| Gate 3（seal 保護水準の受容） | `gate3_seal_acceptance.json` | D2 runner の続行（C0 freeze の**後**に成立する概念のため、C0 manifest / freeze event のいずれにも埋め込まれない）。**freeze 後に発行すること（unseal が順序を検証する）**（`UNDERSPEC-CAL-D85`） |

## スキーマ

全ファイル共通のフィールド（`approvals.ApprovalRecord` 参照）:

```json
{
  "gate": "GATE1_CAMPAIGN_EXECUTION",
  "approver": "ユーザー記入",
  "approved_at_utc": "ユーザー記入 (ISO 8601)",
  "design_doc_sha256": "ユーザー記入 (現在の DESIGN_VG_METER_CAL_DEBT_v1.0.md の sha256)",
  "memo_sha256": "ユーザー記入 (現在の IMPLEMENTATION_MAP_v1.md の sha256)"
}
```

`design_doc_sha256`/`memo_sha256` は loader（`approvals.load_approval()`）が
**実ファイルの実測 hash と照合**する（不一致 → 未承認、理由を列挙）。
`campaign_id` は含まない — campaign_id は manifest 側の派生値であり、承認
ファイルより先に存在しなければならない循環関係を持ち込まないため
（PR レビュー第 2 巡）。

Gate 固有の追加フィールドと、記入例・厳密なスキーマ + 具体的な JSON 例は
[`GATE_REVIEW_BRIEF_v1.md`](../GATE_REVIEW_BRIEF_v1.md) §6 を参照。

## freeze 失敗後の staging cleanup（round 15 finding #4 見送り・境界宣言。`[UNDERSPEC-CAL-D33]`）

`c0_freeze.py` の armed freeze は成果物をまず `.staging-<id>-*`（campaign 側は
`campaigns/.staging-<id>/`、secret 側も同様）へ書き、read-back 検証を通ってから
`os.replace` で公開する。**marker 作成（公開直前）が失敗すると、この staging dir
だけが残ることがある**。この staging dir は 0700 の secrets root / campaigns dir
配下にのみ存在し、一切 publish（公開先パスへの `os.replace`）されないため secret
相当の情報を外部へ晒すことはないが、自動 cleanup の対象ではない
（`detect_orphans()` は `.staging-*` を明示的に除外する）。失敗した freeze の
後始末は運用者が手動で行う:

```bash
rm -rf "$VG_CAL_SECRET_DIR"/.staging-<id>-* "<campaigns_dir>"/.staging-<id>
```

## E_use evidence table の source digest 再刻印（round 20 採用, `[UNDERSPEC-CAL-D46]`）

`config/e_use_table_v1.json` の `USER_ACCEPTED_USE_BOUND` 行（`source_id_or_url`
が `"GATE1-DELEGATION-..."` で始まる行）は、[`records/GATE1_DECISION_RECORD.md`](records/GATE1_DECISION_RECORD.md)
§4 の規約により `source_hash_or_version` にその決定記録ファイル自体の
sha256 を引用する。決定記録を編集すれば digest は当然動くため、
`c0_freeze` dry-run/armed 双方（`e_use_table.validate_source_digests()`
経由）は不一致を `E_USE_SOURCE_DIGEST_MISMATCH` として fail-closed で
検出する——古い digest のまま freeze することはできない。

再刻印は以下の順序で行う（**この順序を守らないと digest がまた動く**:
決定記録を確定させる前に再刻印すると、その後の記録編集で再び不一致になる）:

1. `records/GATE1_DECISION_RECORD.md` を確定させる（以降このコミットでは
   編集しない）
2. 再刻印コマンドを実行する:

   ```bash
   python -m voice_genesis.calibration.e_use_table restamp
   # --table-path/--source/--repo-root で既定パスを上書き可能。既定は
   # それぞれ voice_genesis/calibration/config/e_use_table_v1.json /
   # voice_genesis/calibration/approvals/records/GATE1_DECISION_RECORD.md。
   # GATE1_DECISION_RECORD.md 自体は書き換えない — source_hash_or_version
   # 列のみを対象行だけ更新する。
   ```

3. `config/e_use_table_v1.json` の diff（`source_hash_or_version` 列のみが
   変わっているはず）を確認して commit する
4. dry-run で検証する:

   ```bash
   python -m voice_genesis.calibration.c0_freeze
   # 出力の blocked_codes に E_USE_SOURCE_DIGEST_MISMATCH が含まれないこと
   ```

## 完了時の CLI 確認手順

```bash
# dry-run（承認未完でも実行可。ブロック理由に不足承認が表示される）
python -m voice_genesis.calibration.c0_freeze

# 武装実行（3 要素: --armed + 環境変数 + 有効な承認ファイルが揃って初めて公開する）
VG_CAL_C0_FREEZE_AUTHORIZED=1 python -m voice_genesis.calibration.c0_freeze --armed
```
