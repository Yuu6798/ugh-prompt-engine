# 承認ファイル（Gate 1–3）— 配置先ガイド

このディレクトリは **場所の説明専用**（IMPLEMENTATION_MAP_v1.md §6.1）。
承認ファイルの実体（JSON）は本リポジトリのどこにも一切格納しない。

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
| Gate 3（seal 保護水準の受容） | `gate3_seal_acceptance.json` | D2 runner の続行（C0 freeze の**後**に成立する概念のため、C0 manifest / freeze event のいずれにも埋め込まれない） |

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

## 完了時の CLI 確認手順

```bash
# dry-run（承認未完でも実行可。ブロック理由に不足承認が表示される）
python -m voice_genesis.calibration.c0_freeze

# 武装実行（3 要素: --armed + 環境変数 + 有効な承認ファイルが揃って初めて公開する）
VG_CAL_C0_FREEZE_AUTHORIZED=1 python -m voice_genesis.calibration.c0_freeze --armed
```
