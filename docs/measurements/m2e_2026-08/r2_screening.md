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

## 4. 全 50 曲の実測（進行中）

| # | track（lexical order） | `residual_db` | 数値判定 | 目視判定 | 採否 |
|---|---|---|---|---|---|
| — | （r2 実行後に全件を埋める） | — | — | — | — |

---

## 5. 未確定（埋めるまで結論を出さない）

- 全 50 曲の `residual_db` 実数値
- 通過 2 件（lexical order 先頭から）
- スペクトログラム各画像の sha256 と 1 行判定
- §3.6.1 のラダー縮退が必要かどうか（通過が 2 件に満たない場合のみ発動）
- 再取得に要した実時間（`S` へ計上する内訳）
