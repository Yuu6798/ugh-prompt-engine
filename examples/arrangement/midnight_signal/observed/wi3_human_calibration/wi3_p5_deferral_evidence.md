# WI3 P5 繰り延べ根拠（committed 裏付け）

PR #202 Codex P2 対応（第4ラウンド）。`wi3_preregistration.yaml` §2 の P5
（実 Suno 正典 form take vs 順序破壊 take）は次のように status を記録している
（**同ファイルは聴取前凍結の歴史的記録のため一切編集しない** — 本ファイルが
その `status` 記述の committed 裏付けを提供する）:

> 本トランシェでは実施しない。P5 に必要な実 Suno 素材
> (suno_A1/A2/B1/B2 等) は sha256 pin のみで Drive 未回収
> （wi3_audio_inventory.md §2.1/§2.5 実地確認済み、Drive 検索 0件ヒット）。
> User の手元 or Drive からの再回収が前提のため、P5 は第2トランシェへ
> 繰り延べる。本トランシェの proxy v0 判定は P5 を含まない。

引用元の `wi3_audio_inventory.md` はこの調査を行ったセッションの scratchpad
限定ファイル（非コミット）であり、fresh checkout から参照できない。本ファイルは
その §2.1/§2.5 の内容を、以下の 2 層に分けて委譲元セッションから転記し、
committed 記録として固定する。

## (a) リポジトリ内で機械検証可能な事実

実 Suno 4 take（`suno_A1` / `suno_A2` / `suno_B1` / `suno_B2`、AR4 検収デモ）の
provenance 正本は
[`observed/suno/takes_memo.yaml`](takes_memo.yaml) であり、各 take エントリは
`sha256` フィールドのみを持ち、`drive_file_id` フィールドを一切持たない
（`tests/test_wi3_human_calibration_fixture.py::
test_suno_p5_materials_have_sha256_pin_and_no_drive_file_id` が本ファイル自身を
機械 pin する — 下記 §3 参照）。

`takes_memo.yaml` 冒頭のコメント（verbatim）:

```yaml
# Plan C demo — received takes memo
# Provenance for the 4 Suno A/B audio files used in this AR4 demo.
# Nothing in this file or directory is committed to the repo.
```

各 take のフィールド一覧（`sha256` はあるが `drive_file_id` は存在しない —
`suno_A1` の実エントリを代表として抜粋）:

```yaml
  - assigned_name: suno_A1
    cell: A (tags)
    original_upload_filename: "826406c2-__A_____.mp3"
    original_upload_path: "/root/.claude/uploads/a0e0f92b-e776-5a6f-a1cf-8505d12bbca8/826406c2-__A_____.mp3"
    assignment_basis: "no numeric suffix -> 1st Cell A take"
    sha256: "25621c50f3e51132626e561aa21017ce727bab594f309b0643b9d01b92708772"
    duration_seconds: 190.960
    duration_measurement_method: "librosa.load(sr=None, mono=True); duration = len(y)/sr"
    sample_rate_hz: 48000
    file_type: "Audio file with ID3 version 2.4.0, contains: MPEG ADTS, layer III, v1, 64 kbps, 48 kHz, Stereo"
```

同ディレクトリの [`README.md`](README.md)「音源について」節（verbatim 抜粋）:

> 音源 4 本（`suno_A1.mp3` / `suno_A2.mp3` / `suno_B1.mp3` / `suno_B2.mp3`）は
> **非コミット**。provenance は `takes_memo.yaml`（各 take の sha256 / 収録時刻 /
> 元アップロードファイル名）と、各観測 JSON の `generated_artifact.sha256` に
> 記録されている。Google Drive 等への退避は follow-up 課題。

`observed/suno/` ディレクトリ全体（`takes_memo.yaml` / `README.md` /
`order_sheet.md` / `handoff_deviations.yaml` / `observed/suno_{A1,A2,B1,B2}_
observation.json` / `package/*.json`）に対して `drive_file_id` を grep した
結果は **0 件ヒット**（本 PR 対応時点で再実行し確認済み — コマンドと結果は
`pr202_fix_verification.md` に記録）。

## (b) セッション attestation（機械検証不能・非再現の外部観測 — 明示ラベル）

以下は 2026-07-21 に実施した、リポジトリ内容からは再現できない外部
（Google Drive アカウントの当時の状態に依存する）観測であり、
**機械検証不能なセッション attestation**として明示的にラベル付けする
（(a) 節の committed 事実とは性質が異なる — 再実行しても同じ結果になる保証は
ない。Drive 側の状態変化に依存するため）。

`mcp__Google_Drive__search_files` で以下 3 クエリを実行（ダウンロードは
していない、2026-07-21 実施）:

1. `fullText contains 'midnight_signal' or title contains 'midnight_signal' or
   title contains 'suno_A1' or title contains 'suno_B1'` → **0 件ヒット**
2. `title contains '826406c2' or title contains '674ed272' or title contains
   '2c427a21' or title contains 'cd1a7a13'`（実 Suno 4 take の元アップロード
   ファイル名 prefix、`takes_memo.yaml` の `original_upload_filename` より） →
   **0 件ヒット**
3. `mimeType contains 'audio/' and (fullText contains 'suno' or fullText
   contains 'midnight')`（補助クエリ、音声ファイルに絞った広域検索） →
   **0 件ヒット**

（参考: さらに広域な `title contains 'suno' or fullText contains 'suno'` は
10 件ヒットしたが、全て docx/Google Doc/markdown/pdf 等のテキスト系ファイルで
あり、音声ファイルは 1 件も含まれていなかった。）

**結論（attestation、再現保証なし）**: 2026-07-21 時点で、調査を行った Google
Drive アカウント上に midnight_signal / suno_A1〜B2 関連の音声ファイルは
確認できなかった。これは (a) 節の committed 事実（`drive_file_id` フィールド
不在）と整合する外部観測であり、それを裏付ける追加情報ではあるが、
Drive アカウントの状態は本リポジトリの外側にあるため、本リポジトリの
内容だけからは再検証できない（fresh checkout で `drive_file_id` grep 0 件を
確認することはできるが、この Drive 検索自体を再実行して同じ 0 件を得られる
保証はしない）。

## 帰結

P5（実 Suno 正典 form take vs 順序破壊 take）は、(a) committed sha256-pin-only
provenance と (b) 2026-07-21 時点の Drive 未回収の外部観測の両方から、
現時点で素材未回収であることが二重に裏付けられる。`wi3_preregistration.yaml`
§2 の P5 status（本トランシェ非実施・第2トランシェへ繰り延べ）はこの状態の
正しい記述であり、本ファイルはその committed 裏付けを提供する
（`wi3_preregistration.yaml` 自体は聴取前凍結の歴史的記録のため変更しない）。
