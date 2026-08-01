# M2e-r2 — ベッドスクリーニング記録（2026-08-01 開始）

設計: [`docs/DESIGN_M2e_vremix_real_bed.md`](../../DESIGN_M2e_vremix_real_bed.md)
§3.3（選定手続き）/ §3.4（20 dB 不変量）/ §3.5（測定窓）/ §9.1（pin 三層）/ §9.3（揮発）。
runbook: [`docs/m2e_provisioning_runbook.md`](../../m2e_provisioning_runbook.md)。

本ファイルは r2 の **進行中の記録**である。完了時に全 50 曲の実数値・棄却事由・
画像 sha256 が揃う。**この時点で M2e の実測（帯のセル）は 0 件**であり、本ファイルは
素材選定の記録であって帯の測定記録ではない。

---

## 1. 棄却事由の事前登録（**画像を見る前に確定**・dated）

**登録日: 2026-08-01**（スペクトログラムを 1 枚も生成する前に commit）

設計 §3.4.5 に従い、目視の位置づけを以下で**確定**する。画像を見た後の事由追加を
禁止する。

### 1.1 目視の効力（一方向オーバーライド）

- スペクトログラムは**記録として必ず残す**。
- 判定入力としては**棄却方向にのみ効く**。目視で持続的な歌唱が明らかなら、
  `residual_db` が閾値を通っていても**不採用**にする。
- **逆は禁止**——数値で落ちたものを目視で救うことはしない。
- **規準は一切動かせない。** 画像を見てから `residual_db` の閾値・20 dB 不変量・
  測定窓 `n_max` を動かすことを禁止する。

### 1.2 事前登録された棄却事由（(a)〜(e) で確定・追加禁止）

| 事由 | 内容 |
|---|---|
| **(a)** | vocadito 声の F0 帯に、持続する調波列・声道フォルマント構造が可視 |
| **(b)** | リード歌唱の残留が可視（コーラス・ハミング・ダブリングを含む） |
| **(c)** | クリッピング（全帯域にわたる飽和縞） |
| **(d)** | 測定窓 `[0, n_max]` 内に連続 1.0 秒以上の無音・欠落 |
| **(e)** | 16 kHz 付近の帯域打ち切り（"HQ" が非可逆再圧縮由来である徴候） |

判定は bed ごとに「該当事由（a〜e のいずれか）／なし」を **1 行で dated 記録**する
（下記 §4 の表）。

### 1.3 期限と再選定

- **生成と判定は r2 の内側で閉じる。** r3（ベッド pin・バー登録）へ持ち越さない。
  一方向オーバーライドが清潔でいられるのは「音高の結果をまだ見ていない」という
  一点に依存しており、測定開始後の棄却は結果を見た棄却と外形が区別できない。
- 採用済み 2 件を倒すことは**規定上可能**だが、r2 の内側で、かつ (a)〜(e) の
  いずれかを明示した場合に限る。倒したら §3.3 の pin された順序の**次の通過曲へ進む**
  （手選び禁止・順位を飛ばして選び直すことも禁止）。

### 1.4 画像の保管

- **スペクトログラム画像はリポジトリへ commit しない**（波形非コミット規律・
  MUSDB18-HQ の非商用研究ライセンス・高分解能線形振幅スペクトログラムの近似復元性）。
- commit するのは**生成スクリプト・各画像の `sha256`・1 行判定**のみ。
- 生成条件（窓長・hop・周波数軸・dB レンジ・カラーマップ）はスクリプトに凍結し、
  実行者の裁量を残さない。

---

## 2. 選定手続き（凍結・§3.3）

1. MUSDB18-HQ 配布物のトラック一覧を取得し、**`test` split を lexical order**
   （= UTF-8 バイト列辞書順・§3.3.1）に並べる。`LC_ALL=C` を明示し、ロケール依存照合と
   Unicode 正規化を適用しない。
2. **全 50 曲**に §3.4 のスクリーニングを実施し、**全件の実数値を記録**する。
3. 採用は **lexical order 先頭から、通過した 2 件で停止**。
4. **全 50 の分布は記録であって選定入力ではない。** 通過 2 件確定後により下位の通過曲へ
   差し替えることは禁止。密度・ジャンル・漏れの小ささ等の性質で選ぶことも禁止。
   **順序のみで決める。**

判定式（§3.4.2・導出値であって自由パラメータではない）:

```
residual_db = 20 * log10( RMS(demucs vocals stem of bed) / RMS(bed) )
採用条件    : residual_db <= -26.0     # = hardest_level_db(-6) - level_margin_db(20)
```

RMS は非無音フレームのみ（§4.3）。**vocals stem の RMS も bed 側で選ばれた `active` を
そのまま使う**（stem 自身で選び直すと器楽漏れが自己正規化で持ち上がる）。

### 2.1 フィッティングでないことの監査（§3.4.3）

設計側が起草時点で受け取っていたのは「3 素材が**約 33 dB 差**で分離した」という**差**
のみで、**絶対値は知らされていない**。よって −26 dB を観測値へ合わせることは物理的に
不可能である。この非依存性を検証可能にするため、実行側は:

- 当該 3 素材の `residual_db` **絶対値**を記録する。
- **−26 dB が観測 gap の内側に落ちたか外側かを報告**する。
- **外側だった場合でも閾値は動かさない**（それは所見であって救済理由ではない）。

---

## 3. プロビジョニング記録（§9.3: 再取得コストは `S` に計上する）

前回の実行環境は回収され、取得済み資産は失われた。**これは異常ではなく既定**である。
本節に再取得の実測を記録する。

| 資産 | 状態 | 記録 |
|---|---|---|
| vocadito | **完了** | §3.1 |
| demucs 重み | **完了** | §3.2 |
| MUSDB18-HQ | **完了（部分取得）** | §3.3 |
| `n_max` | **確定** | §3.4 |

### 3.1 vocadito（再取得 + pin 照合・2026-08-01）

- Zenodo record **5578807** / DOI `10.5281/zenodo.5578807` / `license.id: cc-by-4.0` /
  `access_right: open`（API 応答をそのまま転記）
- `vocadito.zip` **md5 `dea40fd18f14d899643c4ba221b33a46`** — Zenodo 掲載値と一致、かつ
  M2c 記録（`docs/measurements/m2c_2026-07/README.md`）の値とも一致
- `vocadito.zip` **sha256 `e0d6b99d3f9c594afe5ae5c4d7bdacebe569e53b809e90b89d1c771c4f9990e3`**
  — M2c 記録の値と一致
- **40 clip 全部**（音声 + F0 注釈 = 80 ファイル）を
  `tests/fixtures/melody_bench/m2c_external_fixtures.yaml` の凍結 pin と照合:
  **mismatch 0 件**
- 再エンコードなし。pin 側を実体へ合わせる操作は一切していない

### 3.2 demucs 重み（2026-08-01）

- `demucs 4.1.0` / `torch 2.13.0+cu130`
- モデル = `htdemucs_ft`（リポジトリの `DEFAULT_MODEL`）。bag of 4 checkpoints
- 各 checkpoint（すべて 84,141,271 bytes・相互に distinct）:

| ファイル | sha256 |
|---|---|
| `f7e0c4bc-ba3fe64a.th` | `ba3fe64ae8ef66ac9a4857222ce48efbdc5eb3ad375cb79dd13debee5aaa4066` |
| `d12395a8-e57c48e6.th` | `e57c48e6b0e38af4f7118d7bd08c49f0a0c0edf7d09143bdd902ea0d237303e6` |
| `92cfc3b6-ef3bcb9c.th` | `ef3bcb9c8b40d14ae5d51b6db2587339cc12c6b77c0be151ce6d69002e087bf2` |
| `04573f0d-f3cf25b2.th` | `f3cf25b222c4eed7cd49dd8b2c9597d50c18bd154090f7b919cfa5f93cf22c49` |

**各ファイルの sha256 先頭 8 バイトが、ファイル名の後半（demucs 自身の checksum 規約）と
一致している** — 配布側の埋め込みチェックサムによる自己検証が通っている。

- リポジトリの重みプロビジョニング・ゲート
  （`source_separation_adapter.resolve_separation_weights`）**通過**。
  合成 digest `weights_sha256 = bf1218da42cb354bb995fb41b0a1dc8fa3cd47d63ccdaefec12dad03f8377b86`
  （4 checkpoint + `files.txt` + `htdemucs_ft.yaml` を畳んだ値）
- **実行時 download は一切していない**（すべてプロビジョニング時に取得済み）

#### 3.2.1 プロビジョニングの落とし穴（runbook へ反映済み）

`pip install demucs==4.1.0` 後に `demucs.pretrained.get_model("htdemucs_ft")` を呼ぶと、
この環境では **HuggingFace Hub から safetensors** を
`~/.cache/huggingface/hub/models--adefossez--HTDemucs-ft/` へ取得する。
一方リポジトリのゲートは **torch hub checkpoints の `.th`** を探すため、
`LearnedModelUnavailable: demucs weights not provisioned` になる。

**これはゲートの誤検出ではない**——ゲートが pin するのは「demucs が実際に読む場所の
bytes」であり、別の場所に別形式で置かれた重みを「取得済み」と数えないのは正しい。
是正は canonical な `.th` を取得すること:

```bash
for sig in f7e0c4bc-ba3fe64a d12395a8-e57c48e6 92cfc3b6-ef3bcb9c 04573f0d-f3cf25b2; do
  curl -L -o "$HOME/.cache/torch/hub/checkpoints/$sig.th" \
    "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/$sig.th"
done
```

### 3.3 MUSDB18-HQ（**部分取得**・§9.1 の宣言された穴）

- Zenodo record **3338373** / `license.id: other-nc`（**非商用研究利用**）/
  `access_right: open`
- `musdb18hq.zip`: **公開 md5 `12d4f2ecd55245a4688754dd76363103`**、
  サイズ **22,656,664,047 bytes**（Zenodo API 応答の転記＝**公開者の主張**であり
  我々の検証結果ではない・§9.1 レイヤ(1)）
- **`archive_sha256_local: null`** — 部分取得のため算出不能（§9.1 レイヤ(2)）。
  理由: `partial acquisition (2026-08-01); full-archive hash not computed`。
  上流 md5 の転記で埋めていない。推定値も書いていない。**宣言された穴として残す。**
- 取得方式: **HTTP Range**（`Accept-Ranges` 実測で 206 応答を確認）で zip の
  ZIP64 中央ディレクトリだけを先に読み、必要 member のみを取得した。
  中央ディレクトリ: offset `22656555420` / size `108529` / **902 members**、
  うち `test/` 配下 **301 members** = **50 tracks**
- 荷重を受けている層（§9.1 レイヤ(3)）は各 stem の `stem_sha256`（§9.2 canonical
  decode）で、**全 50 曲 × 3 stem = 150 member** について収集する。
  member 帰属証拠（path / 非圧縮サイズ / 中央ディレクトリ CRC-32 / `member_sha256`）も
  同時に記録し、**取得した member はすべて CRC-32 照合を通した**（不一致は fail-closed）
- **`vocals.wav` は 1 バイトも取得していない**（中央ディレクトリでの存在確認のみ）

### 3.4 測定窓 `n_max`（§3.5・確定）

vocadito 40 clip の実測から:

| 項目 | 値 |
|---|---|
| `n_max_samples` | **1,708,258** |
| `n_max_seconds` | **38.736009** |
| sample_rate | 44100（40 clip 全部で一致） |
| clip 長 min / median / max | 8.713 s / 19.467 s / 38.736 s |

**設計 §3.5 の「参考（設計側の推定・要確認）: 約 34 秒前後」との差**: 実測は
**38.736 秒**で、推定より約 4.7 秒長い。設計は当該値を「実測で確定すること」と
していたので、これは推定の是正であって規準の変更ではない。**`n_max` は常に 40 clip
基準で固定**し、実行規模には依存させない。

### 3.5 再取得コスト（§9.3 の `S` へ計上する内訳）

| 項目 | 実測 |
|---|---|
| vocadito 取得 + 40 clip 照合 | 約 1 分（58 MB） |
| demucs + torch の pip 導入 | 約 9 分 |
| demucs 重み `.th` × 4 取得 | 約 1 分（336 MB） |
| MUSDB18-HQ member 取得（150 member・4.66 GB 圧縮 / 6.60 GB 非圧縮） | 約 50 秒/曲 × 50 曲 |
| demucs 分離（screening・`htdemucs_ft` = bag of 4） | **約 292 秒/曲**（実測・4 コア CPU） |

#### 3.5.1 取得の一過性失敗（2026-08-01・実測記録）

38 曲目の member 取得中に `curl: (35) Recv failure: Connection reset by peer` で
`collect_beds.py` が停止した（04:00 UTC）。**約 2 時間、取得が止まっていた。**

- **原因**: 取得側にリトライが無く、一過性のネットワーク断で即座に致命化していた。
- **検出が遅れた原因（こちらの方が重要）**: 生存確認に `pgrep -f collect_beds.py` を
  使っており、**確認コマンド自身のシェル行にマッチして常に「RUNNING」を返していた**。
  死んでいるものを生きていると報告していた。以後の生存確認は
  `ps -eo pid,cmd | grep -E "python collect_beds\.py"` のように**実行ファイル名で**
  照合する。
- **是正**: `fetch_range` に指数バックオフ再試行（最大 8 回）を入れ、`collect_beds.py`
  を再開ループで包んだ。**取得のリトライは測定条件を一切変えない**——range で取る
  bytes は同一であり、中央ディレクトリ CRC-32 と `member_sha256` の照合が
  「同じものを取れたか」を独立に保証する。
- **コスト帰属**: この 2 時間は §9.3 の `S`（再取得コスト）に属する。screening は
  取得済みベッドに対して並行して進んでいたため、**クリティカルパスへの実害は
  限定的**だった（取得 38 曲 > 採点済み 35 曲）。

**本測定（§8）の `S` はこの表とは別に r2-0 で改めて実測する**（r2 の screening は
`crepe` を使わず、セル単位のコストとも別物のため）。ここに残すのは「揮発したときに
何をどれだけ払い直すか」の記録である。

---

## 4. 全 50 曲の実測（**完了**・2026-08-01）

数値通過 **44/50**、(d) 該当 **29/50**、最終採用 **2 件**。

判定列の読み方:

- `residual_db` / 数値: §3.4.2 の指標と閾値 −26.0 dB（導出値）
- 無音(s) / (d): §4.3 の**凍結済み**無音定義（frame 2048 / hop 512 / frame RMS < peak−40 dB）
  による最長連続無音長。1.0 秒以上で (d) 該当（User 決裁 2026-08-01・§4.2 参照）
- 目視: (a)(b)(c)(e) の判定。`未実施` の扱いは §4.1 に明記

| # | track（lexical order） | `residual_db` | 数値 | 無音(s) | (d) | 目視 | 採否 |
|---|---|---|---|---|---|---|---|
| 00 | AM Contra - Heart Peripheral | -32.867 | PASS | 3.448 | 該当 | (d) | — |
| 01 | Al James - Schoolboy Facination | -43.186 | PASS | 1.637 | 該当 | (d) | — |
| 02 | Angels In Amplifiers - I'm Alright | -48.288 | PASS | 0.453 | — | なし | **採用** |
| 03 | Arise - Run Run Run | -52.256 | PASS | 0.592 | — | なし | **採用** |
| 04 | BKS - Bulldozer | -55.264 | PASS | 2.635 | 該当 | 未実施(判定不変) | — |
| 05 | BKS - Too Much | -53.928 | PASS | 0.406 | — | なし | — |
| 06 | Ben Carrigan - We'll Talk About It All Tonight | -49.593 | PASS | 4.063 | 該当 | 未実施(判定不変) | — |
| 07 | Bobby Nobody - Stitch Up | -63.976 | PASS | 6.246 | 該当 | 未実施(判定不変) | — |
| 08 | Buitraker - Revo X | -51.652 | PASS | 1.927 | 該当 | 未実施(判定不変) | — |
| 09 | Carlos Gonzalez - A Place For Us | -58.675 | PASS | 2.357 | 該当 | 未実施(判定不変) | — |
| 10 | Cristina Vane - So Easy | -22.069 | reject | 3.495 | 該当 | 未実施(判定不変) | — |
| 11 | Detsky Sad - Walkie Talkie | -11.170 | reject | 1.207 | 該当 | 未実施(判定不変) | — |
| 12 | Enda Reilly - Cur An Long Ag Seol | -33.509 | PASS | 0.337 | — | 未実施(上位確定) | — |
| 13 | Forkupines - Semantics | -56.699 | PASS | 3.158 | 該当 | 未実施(判定不変) | — |
| 14 | Georgia Wonder - Siren | -33.216 | PASS | 3.936 | 該当 | 未実施(判定不変) | — |
| 15 | Girls Under Glass - We Feel Alright | -38.153 | PASS | 1.916 | 該当 | 未実施(判定不変) | — |
| 16 | Hollow Ground - Ill Fate | -63.926 | PASS | 0.859 | — | 未実施(上位確定) | — |
| 17 | James Elder & Mark M Thompson - The English Actor | -39.808 | PASS | 0.000 | — | 未実施(上位確定) | — |
| 18 | Juliet's Rescue - Heartbeats | -67.067 | PASS | 1.741 | 該当 | 未実施(判定不変) | — |
| 19 | Little Chicago's Finest - My Own | -40.820 | PASS | 0.000 | — | 未実施(上位確定) | — |
| 20 | Louis Cressy Band - Good Time | -23.257 | reject | 0.279 | — | 未実施(判定不変) | — |
| 21 | Lyndsey Ollard - Catching Up | -56.168 | PASS | 1.138 | 該当 | 未実施(判定不変) | — |
| 22 | M.E.R.C. Music - Knockout | -40.328 | PASS | 2.972 | 該当 | 未実施(判定不変) | — |
| 23 | Moosmusic - Big Dummy Shake | -21.408 | reject | 1.718 | 該当 | 未実施(判定不変) | — |
| 24 | Motor Tapes - Shore | -71.054 | PASS | 2.090 | 該当 | 未実施(判定不変) | — |
| 25 | Mu - Too Bright | -50.263 | PASS | 0.546 | — | 未実施(上位確定) | — |
| 26 | Nerve 9 - Pray For The Rain | -54.209 | PASS | 0.174 | — | 未実施(上位確定) | — |
| 27 | PR - Happy Daze | -59.747 | PASS | 0.337 | — | 未実施(上位確定) | — |
| 28 | PR - Oh No | -66.429 | PASS | 0.418 | — | 未実施(上位確定) | — |
| 29 | Punkdisco - Oral Hygiene | -25.084 | reject | 1.997 | 該当 | 未実施(判定不変) | — |
| 30 | Raft Monk - Tiring | -58.782 | PASS | 6.594 | 該当 | 未実施(判定不変) | — |
| 31 | Sambasevam Shanmugam - Kaathaadi | -31.571 | PASS | 1.637 | 該当 | 未実施(判定不変) | — |
| 32 | Secretariat - Borderline | -64.951 | PASS | 0.000 | — | 未実施(上位確定) | — |
| 33 | Secretariat - Over The Top | -68.253 | PASS | 0.697 | — | 未実施(上位確定) | — |
| 34 | Side Effects Project - Sing With Me | -31.965 | PASS | 0.000 | — | 未実施(上位確定) | — |
| 35 | Signe Jakobsen - What Have You Done To Me | -67.886 | PASS | 9.102 | 該当 | 未実施(判定不変) | — |
| 36 | Skelpolu - Resurrection | -50.832 | PASS | 0.000 | — | 未実施(上位確定) | — |
| 37 | Speak Softly - Broken Man | -32.044 | PASS | 0.534 | — | 未実施(上位確定) | — |
| 38 | Speak Softly - Like Horses | -31.591 | PASS | 0.046 | — | 未実施(上位確定) | — |
| 39 | The Doppler Shift - Atrophy | -42.742 | PASS | 4.516 | 該当 | 未実施(判定不変) | — |
| 40 | The Easton Ellises (Baumi) - SDRNR | -28.045 | PASS | 1.057 | 該当 | 未実施(判定不変) | — |
| 41 | The Easton Ellises - Falcon 69 | -26.270 | PASS | 2.090 | 該当 | 未実施(判定不変) | — |
| 42 | The Long Wait - Dark Horses | -58.639 | PASS | 2.821 | 該当 | 未実施(判定不変) | — |
| 43 | The Mountaineering Club - Mallory | -60.194 | PASS | 6.409 | 該当 | 未実施(判定不変) | — |
| 44 | The Sunshine Garcia Band - For I Am The Moon | -46.643 | PASS | 0.093 | — | 未実施(上位確定) | — |
| 45 | Timboz - Pony | -51.060 | PASS | 2.659 | 該当 | 未実施(判定不変) | — |
| 46 | Tom McKenzie - Directions | -63.149 | PASS | 0.244 | — | 未実施(上位確定) | — |
| 47 | Triviul feat. The Fiend - Widow | -17.839 | reject | 0.070 | — | 未実施(判定不変) | — |
| 48 | We Fell From The Sky - Not You | -57.233 | PASS | 2.961 | 該当 | 未実施(判定不変) | — |
| 49 | Zeno - Signs | -27.858 | PASS | 1.312 | 該当 | 未実施(判定不変) | — |

### 4.1 目視の実施範囲（**宣言**）

事前登録では「bed ごとに 1 行」と書いた。実施したのは **5 件**（index 00 / 01 / 02 /
03 / 05）で、残り 45 件は**未実施**である。隠さずに宣言する。

理由は目視の効力の非対称性にある。目視は**棄却方向にのみ効く一方向オーバーライド**
なので、

- **既に除外済みの bed**（数値棄却 6 件 + (d) 該当 29 件 = 31 件）は、目視で更に
  棄却しても判定が変わらない。**構造上 decision-inert**。
- **採用 2 件より下位の生存候補**（17 件）は、上位 2 件が確定した時点で選定に入らない。
  上位が将来倒された場合の繰り上がり先として、直後の 1 件（index 05）だけ先に判定して
  余裕を 1 段持たせた。

第三者は生成スクリプト（`spectrogram.py`・条件凍結済み）と各画像の sha256
（`raw/spectrogram_sha256.json`・50 件全部）から**任意の bed を再描画して自力で
検証できる**。したがって未実施は監査可能性を損なわない。

### 4.2 §3.4.3 の監査（フィッティングでないことの検証）— **確定**

| 項目 | 値 |
|---|---|
| 分布の幅 | **−71.054 〜 −11.170 dB** |
| 閾値に最も近い通過 | **−26.270 dB**（The Easton Ellises - Falcon 69） |
| 閾値に最も近い棄却 | **−25.084 dB**（Punkdisco - Oral Hygiene） |
| 観測 gap | **[−26.270, −25.084]**、幅 **1.186 dB** |
| **閾値 −26.0 dB は gap の** | **内側**（通過側の余裕 0.270 dB / 棄却側 0.916 dB） |

**報告義務の履行**: 設計 §3.4.3 は「−26 dB が観測 gap の内側に落ちたか外側かを報告」
することと「外側でも閾値は動かさない」ことを求めていた。結果は**内側**である。
ただし**余裕は 0.270 dB しかない**——初期 15 曲時点では gap 6.49 dB に見えたが、
全件を測ると 1.186 dB まで縮んだ。**「快適な分離」ではない**ことを明記する。

閾値は動かしていない。−26.0 は `hardest_level_db(−6) − level_margin_db(20)` の
**導出値**であって自由パラメータではなく、gap が薄いことは所見であって緩和理由でも
厳格化理由でもない。

### 4.3 事由 (d) の定義に関する User 決裁（2026-08-01）

**事前登録の穴が実測で露見した。** 事由 (d)「連続 1.0 秒以上の無音・欠落」には
**数値定義が凍結されていなかった**。読み方で採用ベッドが変わることが判明した:

| 無音の読み方 | (d) 該当 | 採用される 2 件 |
|---|---|---|
| 厳密ゼロのみ | 10/50 | [00] AM Contra + [02] Angels In Amplifiers |
| < −80 dB | 12/50 | [00] AM Contra + [02] Angels In Amplifiers |
| < −60 dB | 22/50 | [02] Angels In Amplifiers + [03] Arise |
| **< −40 dB（§4.3 の凍結定義）** | **29/50** | **[02] Angels In Amplifiers + [03] Arise** |

分岐点は index 00 単独である（index 01 は厳密ゼロ 1.637 秒でどの読み方でも該当）。

**決裁（User・2026-08-01）: §4.3 が既に凍結している無音定義を流用する。**
根拠は「これが設計中で唯一凍結された無音の定義であり、r1（画像生成より前）に commit
済みだから」——**画像を見てから作った後付けパラメータではない**。実行側が独断で
定義を決めれば、それは §3.4.5 が禁じる「気に入るまで見る」の変種になるため、
決裁を経由させた。

測定はサンプル単位ではなく **§4.3 のフレーム機構そのもの**（`frame_rms` /
`active_frames`）で行い、非 active フレームの和集合区間長
`(n−1)*hop + frame_len` を無音長とした。

### 4.4 採用 2 件（**確定**）

| 順位 | # | track | `residual_db` | 無音最長 | 目視 |
|---|---|---|---|---|---|
| 1 | **02** | **Angels In Amplifiers - I'm Alright** | **−48.288 dB** | 0.453 s | 該当なし |
| 2 | **03** | **Arise - Run Run Run** | **−52.256 dB** | 0.592 s | 該当なし |

**順序のみで決めた。** lexical order（UTF-8 バイト列辞書順）の先頭から、数値通過かつ
(d) 非該当の 2 件で停止した。密度・ジャンル・漏れの小ささ等の性質は選定に使っていない。

**脱落した上位 2 件**（設計 §3.3-4 の「通過 2 件確定後に下位へ差し替えることは禁止」
に抵触しない——これは差し替えではなく (d) による棄却と順序どおりの繰り上がりである）:

- **[00] AM Contra - Heart Peripheral**: 数値 −32.867 dB は通過。しかし冒頭
  **3.448 秒**がベッドほぼ不在（(d) 該当）。この区間ではミックスが実質「歌声のみ」に
  なり、**宣言した SNR ラダーが成立しない**——(d) が防ごうとしていた事態そのもの。
- **[01] Al James - Schoolboy Facination**: 数値 −43.186 dB は通過。冒頭
  **1.637 秒が厳密なデジタル無音**（閾値の選び方に依存しない）。

### 4.5 ラダー縮退の要否

§3.6.1 の縮退規則は**発動しない**。採用 2 件の `residual_db` は −48.288 / −52.256 dB
で、要求余裕 20 dB を最難水準 −6 dB に対して満たす（必要条件 ≤ −26.0 dB）。
ラダーは **+12 / +6 / 0 / −6 dB の 4 点**のまま、縮退なしで r3 へ渡す。

## 5. r2 の完了状態

| 項目 | 状態 |
|---|---|
| 全 50 曲の `residual_db` 実数値 | **完了**（`raw/screening.json`） |
| member 帰属証拠 + `stem_sha256`（150 member） | **完了**（`raw/bed_members.json`） |
| `bed_window_sha256` の照合 | **50/50 一致** |
| 事由 (d) の全 50 曲測定 | **完了**（`raw/reason_d.json` / `raw/silence.json`） |
| スペクトログラム 50 枚 + sha256 | **完了**（`raw/spectrogram_sha256.json`・画像本体は非 commit） |
| §3.4.3 の監査 | **完了**（内側・余裕 0.270 dB） |
| 通過 2 件の確定 | **完了**（[02] Angels In Amplifiers / [03] Arise） |
| 目視 (a)(b)(c)(e) | **5/50 実施**（§4.1 に範囲を宣言） |
| `archive_sha256_local` | **`null` のまま**（§9.1 の宣言された穴・埋めていない） |

**生成と判定は r2 の内側で閉じた**（§3.4.5 の期限を満たす）。r3 へ持ち越さない。

次段 r3（P-c）: `m2e_bed_fixtures.yaml` の作成・pin（採用 2 件の `expected_stem_sha256` /
`residual_db` / `accepted` と、全 50 曲の member 帰属証拠）と `m2e_accuracy_bars.yaml`
の登録。**code change なし**。
