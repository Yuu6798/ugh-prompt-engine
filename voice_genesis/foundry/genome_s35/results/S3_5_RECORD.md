# S3.5 RECORD — Perceptual Gene Gate（人間負担軽量版）

- schema: `voicegenesis-genome-s35/2.0` / protocol: `voicegenesis-s35-v2`
- s3_results_sha256: `65b91402f2b6ead2b8d3269455413e6bd0ae575d66a33a52f0a2367c91e55cd0`
- blind_manifest_sha256: `4000e70d2df2c1e8dbe94b63104596bde3570e7831074ab8be1ef5f7a894bf63`
- answers_sha256 (stage 1): `7054b9ead09ac8e52113d2b89df6c88c2ddbc40fcb241b50155cfa526dbb94a4`
- answers_sha256 (stage 2): `a5d5550493d9ff723d590ccffb88fb6ff19ce04e1bba1b69a277649d6a123fc4`
- key_commitment: `d4fadda8c6d1f09a44289bd56aff47e237bb8cef03f6dadf8207d5447abd15da`
- key_reveal_sha256: `a62af58094dfcb6340e6a71f0c289c386aa8b856c096ab78c8c014a12fecb7ee`
- commitment_verified: **True** / audio_verified: **True**
- listener: `listener-01` / session: `s35-2026-08-21-chat`

## Overall

**S4_READY** — perceptible_candidate_count = 2（必要 2）: duration, f0

> **S4_READY — 少なくとも 2 つの Performance gene について、異なる 2 文脈で単独介入差を人間が識別できた。**

`S4_NOT_READY` は S3 FAIL を意味しない。S3.5 は S3 を覆さない。

**統計的有意差・知覚閾値・完全独立は主張しない。**

## Gene-Level

| gene | Stage 1 | Stage 2 | contexts | verdict |
|---|---|---|---|---|
| release | 不正解 | — | terminal_N | NOT_ESTABLISHED |
| f0 | 正解 | 正解 | terminal_i, terminal_ri | PERCEPTIBLE_CANDIDATE |
| energy | 不正解 | — | terminal_ri | NOT_ESTABLISHED |
| duration | 正解 | 正解 | terminal_i, terminal_ri | PERCEPTIBLE_CANDIDATE |

判定規則: Stage 1 正解 **かつ** Stage 2 正解 → `PERCEPTIBLE_CANDIDATE` / どちらか不正解または `UNSURE` → `NOT_ESTABLISHED` / Stage 2 用の別 context が無い → `NOT_EVALUABLE_S35`。

## Selected pairs（決定論選択・人間は選んでいない）

### release

- Stage 1: `terminal_N|BRD#664|pjs017#27`
- Stage 2: `—`

### f0

- Stage 1: `terminal_i|1st_color#218|pjs001#73`
- Stage 2: `terminal_ri|2018#215|pjs003#65`

### energy

- Stage 1: `terminal_ri|2018#598|pjs065#29`
- Stage 2: `—`

### duration

- Stage 1: `terminal_i|1st_color#218|pjs001#73`
- Stage 2: `terminal_ri|2018#598|pjs065#29`

## S4 へ渡すもの

`S3 SUPPORTED` かつ `S3.5 PERCEPTIBLE_CANDIDATE` の gene。`NOT_ESTABLISHED` の gene は削除せず保持する。

- `release`: mechanistically_supported = true / perceptually_established = false
- `f0`: mechanistically_supported = true / perceptually_established = true
- `energy`: mechanistically_supported = true / perceptually_established = false
- `duration`: mechanistically_supported = true / perceptually_established = true

## 主張禁止

S4_READY でも次は言わない: gene を人間が意味分類できる / gene が知覚上完全独立 / 自然 / 高品質 / 改善 / 歌唱技能を獲得 / Genome Architecture 完成 / 統計的有意 / 知覚閾値を測った。

## Out-of-scope observations（記録のみ・修正しない）

- X は A か B と byte-identical なので、聴取者が 3 ファイルを sha256 で突き合わせれば聴かずに正答できる。commitment 方式が守るのは「実験者が回答後に正解を変えないこと」であって聴取者の自己申告ではない。プロトコル変更は範囲外のため実装では手を付けず、記録にのみ残す。
- 1 gene あたり 2 問なので、偶然の一致でも 1/4 で PERCEPTIBLE_CANDIDATE に到達する。本 Gate は統計的証明ではなく、S4 へ進むための最小確認である。

## Notes

- raw WAV は commit しない（`results/.gitignore`）。波音リツ 歌声データベース利用規約 第3条1（転載禁止）にも該当。
- `answer_key.private.json` は commit しない。
- 音源は S3 canonical WAV の byte copy のみ。再生成・normalize・gain・trim・fade・denoise・resample はしていない（SHA 一致を検査済み）。
- 結果を見たあとの pair 入れ替え・閾値緩和は禁止。必要なら新規事前登録の別実験として行い、本結果は残す。
