# M2e プロビジョニング runbook（実行者非依存）

設計: [`DESIGN_M2e_vremix_real_bed.md`](DESIGN_M2e_vremix_real_bed.md)（統合正本）。
本書は設計 §10 の **M2e-r0** が要求する runbook である。

**目的**: 「実行者は交換可能」を成立させること。M2e は実ベッド（MUSDB18-HQ）を使う
ため、合成ベッドと違い **spec + コードだけでは再現できない**（設計 §9.2 末尾の
トレードオフ明示）。この落差を埋めるのが本書であり、おまけではない。

**規律**: 本書は手順のみを記述する。**閾値・バー・規模を本書で決めない**（それらは
設計 §3〜§8 が凍結する）。本書と設計が食い違ったら**設計が勝つ**。

---

## 0'. コンテナが揮発したときの復旧（最初に読む）

**実行環境の回収は異常ではなく既定**である（設計 §9.3）。重み・展開済み
MUSDB18-HQ・生成済みミックスが消えているのを見ても、「素材が無いので着手できない」と
結論してはならない。**その結論は対処を間違える。** 正しい手順:

1. **段階を確認する。** r0 / r1 は**音源ゼロで完走する**。揮発は r0 / r1 を止める
   理由に**ならない**。止まって見えるなら切り分けが誤っている。
2. **要る資産をその段階の分だけ取る。** 全 50 曲が要るのは **r2 だけ**。
   r3 以降は採用 2 曲 × 3 stem + vocadito（数百 MB）。
3. **再取得にかかった時間を `S` に計上する**（§8.4 の `S` を §9.3 が拡張）。
   `cap = 0.85 * B_session - S` が自動的に吸収する。**係数 0.85 も `B_session` も
   規模（1280 セル）も動かさない。**
4. **`S` だけで `B_session` を食い切ったら**（`cap <= 0` または
   `cap < max(T_direct, T_stem)`）、§8.5 の既存 fail-closed どおり**開始せず**
   `S` / `T_*` / `P` を添えて User 決裁へ差し戻す。新しい例外経路を作らない。
5. **完了済みセルは失われていない。** セルレコードは commit 済みの成果物であり、
   `env_digest` が一致すれば §8.7 の再開規則でそのまま飛ばせる。揮発で失われるのは
   **入力資産**であって**測定結果**ではない（結果を揮発領域にだけ置かないこと）。

再取得の重さの実態（**重いのは取得であって計算ではない**）:

| 資産 | 重さ | 手順 |
|---|---|---|
| vocadito | 軽い | §3（repo の pin と照合するだけ。再エンコード禁止） |
| demucs / crepe | 軽い | §2（pip + 重み取得 + digest 記録） |
| MUSDB18-HQ | **重い（ここだけ）** | §4。r2 のみ全 50 曲。r3 以降は採用 2 曲 |

---

## 0. 前提と非目標

- 本 runbook はプロビジョニング（素材・重み・環境を揃える）までを扱う。
  スクリーニング判定（設計 §3.4）・バー登録（§5.3）・本測定（§8）は各段階の
  dated 記録が別途要る。
- **本書のどの手順も、`docs/DESIGN_M2e_vremix_real_bed.md` が commit された後にのみ
  実行してよい**（設計 §0: リポジトリにない事前登録は事前登録ではない）。
- ネットワーク取得はすべて **プロビジョニング時のみ**。ハーネスの実行時ダウンロードは
  既存規律どおり禁止（`LearnedModelUnavailable` として正直に落とす）。

---

## 1. Python 環境と lockfile

```bash
python -V                      # 3.11 系（既存 M2b/M2c 実測は 3.11.15）
pip install -e ".[dev,separate]"
pip install crepe              # manual/external 統合（published extra なし）
```

- **Melodia / essentia は入れない。** 設計 §2 ③ で deferred へ棚上げ済み。
- 設計 §8.7 の `env_digest` は **M2e-r4（r2-0）で確定**し、同時に lockfile を commit
  する。lockfile の生成は環境を固めた直後に行う:

```bash
pip freeze > docs/measurements/m2e_2026-08/requirements.lock.txt
```

`env_digest` に入れるもの（設計 §8.7・**この一覧を減らさない**）:
Python 版 / torch / demucs / crepe / librosa / soundfile / numpy の版 /
demucs 重み digest / crepe 重み digest / スレッド設定。

スレッド設定は決定論の前提条件であって性能設定ではない（設計 §8.3）。全ワーカーで:

```bash
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
# Python 側: torch.set_num_threads(1)
```

---

## 2. 重みのプロビジョニング

重みは**リポジトリに入れない**。取得し、digest を記録する。

### 2.1 CREPE

`pip install crepe` は重み（`model-full.h5` 等）を同梱しないビルドがある。
アダプタは「インストール済みだが重み未配置」を **3 値目の unavailable** として
正直に落とす（`docs/melody_observability.md` §6.4）。実行前に:

1. crepe パッケージディレクトリ配下へ `model-full.h5` を配置する。
2. **ファイル単体 sha256 とバイト数**を記録する（M2b 記録と同じ様式）。

```bash
python - <<'PY'
import hashlib, pathlib, crepe
root = pathlib.Path(crepe.__file__).parent
for p in sorted(root.rglob("model-*.h5")):
    print(p.name, p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
PY
```

**重みライセンスは未 inspect のまま**（policy §4: permissive なコードは permissive な
重みを含意しない）。M2e はこの状態を変えない——`weights_license` は fail-closed で
「未検証」を明示したまま使う。

### 2.2 Demucs

```bash
pip install -e ".[separate]"
```

重みは初回に torch hub キャッシュへ落ちる。**本測定の前に取得を済ませ**、
digest を記録する（実行時ダウンロードは禁止）。

```bash
python - <<'PY'
import hashlib, pathlib, os
cache = pathlib.Path(os.environ.get("TORCH_HOME", pathlib.Path.home() / ".cache/torch"))
for p in sorted(cache.rglob("*.th")) + sorted(cache.rglob("*.pt")):
    print(p, p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
PY
```

### 2.3 チャンネル受け渡し規約

モノ信号を demucs へ渡す複製規約と出力 stem のモノ化規約は、**既存
`demucs_vocals_then_crepe` 経路の実装をそのまま流用する**（設計 §4.7）。
新規約を発明しない。流用元の該当箇所（ファイル・関数）を実測記録に明示すること。

---

## 3. vocadito（歌声・正解側）

- **既存 pin をそのまま使う。再取得・再エンコード禁止**（設計 §3.1・§13）。
- pin の所在: `tests/fixtures/melody_bench/m2c_external_fixtures.yaml`
  （`m2c-external-fixtures/0.1`・`registered_utc: 2026-07-29`・40 clip）。
- 取得元と照合値は M2c の dated 記録
  [`docs/measurements/m2c_2026-07/README.md`](measurements/m2c_2026-07/README.md)
  にある（Zenodo record 5578807・CC BY 4.0・`vocadito.zip`
  md5 `dea40fd18f14d899643c4ba221b33a46`）。

手元の展開物が pin と一致することの確認（**一致しないなら止まる**。直さない）:

```bash
python - <<'PY'
import hashlib, pathlib, sys, yaml
root = pathlib.Path("tests/fixtures/melody_bench/m2c_external_fixtures.yaml")
doc = yaml.safe_load(root.read_text())
V = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "./vocadito")   # 展開先
bad = 0
for clip_id, entry in sorted(doc["fixtures"].items()):
    audio = V / "Audio" / f"{clip_id}.wav"
    anno  = V / "Annotations" / "F0" / f"{clip_id}_f0.csv"
    for path, key in ((audio, "expected_audio_sha256"), (anno, "expected_annotation_sha256")):
        got = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
        if got != entry[key]:
            bad += 1
            print("MISMATCH", path, got, "!=", entry[key])
print("mismatches:", bad)
PY
```

> 実ファイル名の並び（`Audio/<clip>.wav` / `Annotations/F0/<clip>_f0.csv`）は配布物の
> 実体に合わせること。**pin 値の側を配布物に合わせて書き換えてはならない。**

---

## 4. MUSDB18-HQ（ベッド・伴奏側）

### 4.1 取得

- Zenodo record **3338373**、`musdb18hq.zip`（約 22.7 GB）。
- ライセンス: **非商用研究利用**。派生物（波形・スペクトログラム画像）を
  リポジトリへ commit しない（設計 §3.4.5・§9）。
- 使うのは **`test` split の `drums.wav` / `bass.wav` / `other.wav`** のみ。
  **`vocals.wav` は読み込まない**（存在確認のみ。設計 §3.2・§13）。

```bash
# 例。取得手段は問わないが、md5 は必ず Zenodo 掲載値と照合する
curl -L -o musdb18hq.zip "https://zenodo.org/records/3338373/files/musdb18hq.zip?download=1"
md5sum musdb18hq.zip          # Zenodo 掲載 md5 と照合し、両方を記録する
sha256sum musdb18hq.zip       # ローカル算出（完全取得できた場合のみ）
```

### 4.2 pin の三層（設計 §9.1）— **穴を宣言する**

| 層 | 対象 | 誰の主張か |
|---|---|---|
| (1) 配布物 | Zenodo 公開 md5 | **公開者**の主張の転記 |
| (2) ローカル容器 | `archive_sha256_local` | 我々の算出 |
| (3) 復号内容 | 各 stem の `stem_sha256` | 我々の算出 ← **荷重はここ** |

- **完全取得を r2 の前提条件に格上げしない。** 22.7 GB を落としても (3) は強くならない。
- 部分取得で (2) が算出できないなら `archive_sha256_local: null` +
  `archive_sha256_local_reason: "partial acquisition (<日付>); full-archive hash not computed"`
  と **dated 理由**を書く。上流 md5 の転記で埋めない。推定値を書かない。空欄で放置しない。
- 部分取得でも成立する帰属証拠として、zip **中央ディレクトリの CRC-32 / member path /
  非圧縮サイズ / `member_sha256`** を各 member について記録する。中央ディレクトリが
  読めないなら `"unavailable"` + 理由（**穴をさらに宣言する**のであって埋めたことにしない）。

```bash
python - <<'PY'
import zipfile, json
with zipfile.ZipFile("musdb18hq.zip") as z:
    rows = {
        i.filename: {"uncompressed_size": i.file_size, "crc32_central_directory": f"{i.CRC:08x}"}
        for i in z.infolist()
        if i.filename.startswith("test/") and i.filename.endswith((".wav",))
    }
print(json.dumps(rows, indent=2, sort_keys=True))
PY
```

### 4.3 `canonical decode`（設計 §9.2・凍結）

`stem_sha256` は **native 整数 PCM のまま** hash する。float 変換は §4 の生成手続きに
属し、pin の外に置く（除数 32768/32767 の曖昧さを pin へ持ち込まないため）。

```
reader   : soundfile / libsndfile（バージョンは env_digest に記録）
dtype    : ファイル固有の PCM 整数幅のまま（int16→int16 / int24→int32・上位詰め）
resample : 禁止。native rate != 44100 なら fail-closed で停止
channel  : そのまま（downmix・並べ替え禁止）
layout   : C 連続・shape (n_frames, n_channels)・little endian
stem_sha256 = sha256(np.ascontiguousarray(x).tobytes())
```

実装は `scripts/make_vremix_fixtures.py` の `canonical_decode()` が正本。
規約の CI 保証は `tests/test_make_vremix_fixtures.py`（音源不要・commit された
数十サンプルの int16 wav fixture に対する既知 sha256 照合）。

```bash
python scripts/make_vremix_fixtures.py stem-sha256 <path/to/drums.wav>
```

### 4.4 ベッド選定（設計 §3.3・順序のみで決める）

1. `test` split のトラック一覧を **lexical order**（= UTF-8 バイト列辞書順・§3.3.1）で並べる。
   shell で並べるなら `LC_ALL=C sort` を明示する。Unicode 正規化を一切適用しない。
2. **全 50 曲**にスクリーニング（§3.4）を実施し、**全件の実数値を記録**する。
3. 採用は lexical order 先頭から、**通過した 2 件で停止**。
4. 分布の性質（密度・ジャンル・漏れの小ささ）で選ぶことは禁止。**順序のみ。**

```bash
LC_ALL=C ls -1 <musdb18hq>/test | LC_ALL=C sort
```

### 4.5 スクリーニング（設計 §3.4）

```
residual_db = 20 * log10( RMS(demucs vocals stem of bed) / RMS(bed) )
採用条件: residual_db <= -26.0     # = hardest_level_db(-6) - level_margin_db(20)
```

- RMS は非無音フレームのみ（§4.3）。**vocals stem の RMS も bed 側で選ばれた
  `active` をそのまま使う**（stem 自身で選び直さない）。
- 測定窓はベッドの `[0, n_max]`。`n_max` = vocadito 40 clip の最長サンプル数。
- 閾値・20 dB 不変量・`n_max` は**分布を見た後に動かさない**。
- **スペクトログラムは r2 の内側で生成・判定まで閉じる**（§3.4.5）。棄却事由
  (a)〜(e) は画像を見る**前に** dated 事前登録し、画像を見た後の追加を禁止。
  目視は**棄却方向にのみ効く一方向オーバーライド**（数値で落ちたものを救うのは禁止）。
- **画像本体は commit しない。** commit するのは生成スクリプト・各画像の sha256・
  1 行判定のみ。

---

## 5. ミックス生成（設計 §4）

生成は `scripts/make_vremix_fixtures.py` が唯一の経路。seed 不使用・自由変数ゼロ。

```bash
python scripts/make_vremix_fixtures.py build \
    --vocadito-root <展開した vocadito> \
    --bed-root      <musdb18hq/test> \
    --bed           <bed_id_1> --bed <bed_id_2> \
    --level         +12dB --level +6dB --level 0dB --level -6dB \
    --out-dir       <リポジトリ外の出力先>
```

出力（すべて**リポジトリ外**。波形は commit しない）:

| 出力 | 内容 |
|---|---|
| `mix/<entry_id>.wav` | 生成ミックス。`entry_id = vremix_{clip_id}_{bed_id}_{level_tag}` |
| `manifest_<level_tag>.json` | 水準ごとに 1 本（40 clip × 2 bed = 80 件）。ハーネスの `--external-manifest` へ渡す |
| `fixtures_<level_tag>.yaml` | 水準ごとの content pin（`m2e-external-fixtures/0.1`）。`--external-fixtures` へ渡す |
| `generation_record_<level_tag>.json` | `waveform_sha256` / `factor` / `V` / `B` / `g_b` の記録（§4.5 の `factor` 偏り確認用） |

規律（**破ったら測定は無効**）:

- リミッタ・ラウドネス正規化・水準ごとの個別正規化は禁止（宣言 SNR が壊れる）。
- 暗黙のリサンプル禁止（rate 不一致は**停止**）。
- 区間は常に**曲頭 0.0s 起点**（「良い区間を探す」ことをしない）。
- `vocals.wav` を読まない。
- `factor` は `(clip_id, bed_id, level)` ごとに記録する。

検算（実装が §4.4 の式どおりか）: `(V*g_v) / (B*g_b) = 10 ** (R_db/20)`。

---

## 6. 本測定（設計 §8）— 実行者向けチェックリスト

本測定を**開始してよい条件**（1 つでも欠けたら開始しない）:

- [ ] 設計正本が commit 済み（§0）
- [ ] `m2e_accuracy_bars.yaml` が登録済み（r3・§5.3）
- [ ] `m2e_bed_fixtures.yaml` が pin 済み（r3・§9）
- [ ] r2-0 完了: `P` 決定・**並列不変性ゲート合格**（ピッチ軌跡 sha256 の完全一致）・
      `S` / `T_direct` / `T_stem` 校正・`env_digest` 確定・lockfile commit（r4・§8.4）
- [ ] `m2e_r2_shard_map.yaml` を **開始前に** commit 済み（r5・§8.5）
- [ ] `N_shards <= R_max (=12)`。超えるなら開始せず User 決裁へ差し戻す（§8.8）

実行:

```bash
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
python scripts/run_melody_accuracy.py \
    --out <out>/m2e_run_<shard>_<repeat>.json \
    --categories V_remix_real_direct V_remix_real_stem \
    --level <+12dB|+6dB|0dB|-6dB> \
    --external-manifest  <out>/manifest_<level_tag>.json \
    --external-fixtures  <out>/fixtures_<level_tag>.yaml \
    --m2e-bars tests/fixtures/melody_bench/m2e_accuracy_bars.yaml
```

- **1 回 = 1 シャード**、壁時計上限 `B_session = 2.0 時間`。**延長しない。**
  超過は異常ではなく設計に織り込まれた通常状態（§8.6）。
- 未完セルは「未完」として記録し（失敗値を書かない）、次回 `env_digest` 一致で再開。
- `env_digest` 不一致のセルを**スキップ扱いにしない**。複数環境のセルを 1 帯として
  合算しない（§8.7）。
- **P-d（実測 PR）に code change を 1 行でも入れたら、その実測は無効**（§10）。

報告（§11）:

- 1280 セル全部が揃うまで**帯の判定を出さない**。出せるのは census のみ。
- 部分集合の平均 RPA・途中の破断曲線・見通しの表明は**禁止**。
- 4 水準は常に全点提示する。

---

## 7. 記録先

| 段階 | 記録先 |
|---|---|
| r0 決裁・撤回理由 | `docs/measurements/m2e_2026-08/README.md` |
| r2 スクリーニング全 50 曲 | `docs/measurements/m2e_2026-08/`（実数値・棄却事由・画像 sha256） |
| r4 r2-0 校正 | 同上（`P` / `S` / `T_*` / 並列不変性ゲート / `env_digest` / lockfile） |
| r6 本測定 | 同上（run report / verdict / セルレコード / census） |
| r7 破断曲線 | 同上（**昇格宣言をしない**。§12 の判断材料として提出） |
