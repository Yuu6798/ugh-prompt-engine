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

### 0'.1 長時間取得を書くときの必須要件（**実測で踏んだ穴**・2026-08-01）

コンテナは揮発するので、**次の環境では同じスクリプトが書き直される**。スクリプト側の
修正では閉じないため、要件としてここに置く。

1. **取得には必ずリトライを入れる。** 一過性のネットワーク断
   （`curl: (35) Recv failure: Connection reset by peer` 等）でプロセスが即死すると、
   長時間の取得が黙って止まる。指数バックオフで最低 8 回は再試行し、その上で
   **再開ループ**（resumable な収集器を落ちたら再起動する）で包むこと。
   **取得のリトライは測定条件を一切変えない**——range で取る bytes は同一であり、
   CRC-32 / `member_sha256` の照合が「同じものを取れたか」を独立に保証する。
2. **生存確認に `pgrep -f <script.py>` を使ってはならない。** 確認コマンド自身の
   シェル行にパターンがマッチし、**死んでいるプロセスを「実行中」と報告する**
   （実測: 2 時間止まっていたのを「RUNNING」と誤報告した）。実行ファイル名で照合する:

   ```bash
   ps -eo pid,cmd | grep -E "python collect_beds\.py" | grep -v grep
   ```

   より確実には、**プロセスの生存ではなく成果物の進捗**（出力 JSON の件数と
   最終更新時刻）を見る。プロセスが生きていても進んでいないことがある。
3. **進捗は揮発領域だけに置かない。** 生データを定期的に repo へ同期 commit する
   （§9.3: 失われるのは入力資産であって測定結果ではない、を実装で保証する）。
4. **長時間待機を伴う下請け（サブエージェント）は、commit まで到達したかを外形で
   確認する。** 実測 2026-08-01: 実装を委譲した下請けが自分のテスト完了を待つ
   ポーリングループから抜けられず、**作業ツリーに未コミットの変更を残したまま
   「完了」通知だけを返した**。報告文を完了の証拠と見なさないこと（§9.3 の
   「完了判定は報告文でなく成果物で行う」と同じ規律）。確認は
   `git log --oneline -1` と `git status --short` の 2 つで足りる。

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
pip install "setuptools<81"    # ← crepe の導入前に必須。理由は下記
pip install crepe              # manual/external 統合（published extra なし）
```

**crepe 導入の落とし穴（2026-08-01 実測・3 回失敗した）**: `pip install crepe` が
`ModuleNotFoundError: No module named 'pkg_resources'` → `metadata-generation-failed`
で落ちる。crepe の `setup.py` が `pkg_resources` を使う一方、**setuptools 81 以降は
`pkg_resources` を削除している**（本環境の既定は 83.0.0）。`--no-build-isolation` でも
解決しない（ビルド分離ではなく実行環境側の欠落のため）。`setuptools<81` を先に入れる
（実測: 80.10.2 で `import pkg_resources` が通る）。

3 回失敗した事実ごと残す——同じ環境で同じ手順を踏めば同じ壁に当たるので、
「なぜ失敗するか」が次の実行者にとっての情報である。

- **Melodia / essentia は入れない。** 設計 §2 ③ で deferred へ棚上げ済み。
- 設計 §8.7 の `env_digest` は **M2e-r4（r2-0）で確定**し、同時に lockfile を commit
  する。lockfile の生成は環境を固めた直後に行う:

```bash
pip freeze > docs/measurements/m2e_2026-08/requirements.lock.txt
```

`env_digest` に入れるもの（設計 §8.7・**この一覧を減らさない**）:
Python 版 / torch / demucs / crepe / librosa / soundfile / numpy の版 /
demucs 重み digest / crepe 重み digest / スレッド設定 /
**CPU 同一性（モデル名 + 命令セットフラグ）**（rev.6 §8.9.3）。

> CPU を含める理由は再現性である。実測 2026-08-01: 同一セッション中にコンテナ実体が
> `Xeon @ 2.80GHz` → `Xeon @ 2.10GHz`（AVX-512 あり）へ入れ替わり、同一セルの壁時計が
> 2.2 倍変動した。**旧実装ではこの 2 つが同一 `env_digest` を持つ**ため、数値経路が
> 分岐しても「同一環境として合算してよい」と誤判定する。

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
pip install -e ".[separate]"      # または pip install demucs==4.1.0
```

**重要（2026-08-01 実測で判明）: `demucs.pretrained.get_model("htdemucs_ft")` を
呼んで重みを落としても、リポジトリのゲートは通らないことがある。**
demucs 4.1.0 は環境によって **HuggingFace Hub から safetensors** を
`~/.cache/huggingface/hub/models--adefossez--HTDemucs-ft/` へ取得するが、
`source_separation_adapter` が探すのは **torch hub checkpoints の `.th`** であり、
`LearnedModelUnavailable: demucs weights not provisioned` になる。

**これはゲートの誤検出ではない。** ゲートが pin するのは「demucs が実際に読む場所の
bytes」であり、別の場所に別形式で置かれた重みを「取得済み」と数えないのが正しい。
canonical な `.th` を明示的に取得すること:

```bash
mkdir -p "$HOME/.cache/torch/hub/checkpoints"
for sig in f7e0c4bc-ba3fe64a d12395a8-e57c48e6 92cfc3b6-ef3bcb9c 04573f0d-f3cf25b2; do
  curl -L -o "$HOME/.cache/torch/hub/checkpoints/$sig.th" \
    "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/$sig.th"
done
# 各ファイルの sha256 先頭 8 バイトがファイル名の後半と一致する（demucs 自身の
# checksum 規約）。一致しなければ取得失敗として扱い、進めない。
sha256sum "$HOME"/.cache/torch/hub/checkpoints/*.th
```

ゲート通過の確認（**ここが通ってから先へ進む**）:

```bash
python -c "
from svp_rpe.rpe.learned.source_separation_adapter import resolve_separation_weights
w = resolve_separation_weights(); print(w.version, w.sha256, list(w.filenames))"
```

**本測定の前に取得を済ませ**、digest を記録する（実行時ダウンロードは禁止）。

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
    --vocadito-root  <展開した vocadito> \
    --bed-root       <musdb18hq/test> \
    --bed            "Angels In Amplifiers - I'm Alright" \
    --bed            "Arise - Run Run Run" \
    --level          +12dB --level +6dB --level 0dB --level -6dB \
    --registered-utc <YYYY-MM-DD> \
    --out-dir        <リポジトリ外の**空**ディレクトリ>
```

出力（すべて**リポジトリ外**。波形は commit しない）:

| 出力 | 内容 |
|---|---|
| `mix/<entry_id>.wav` | 生成ミックス。`entry_id = vremix_{clip_id}_{bed_id}_{level_tag}` |
| `manifest_<level_tag>.json` | 水準ごとに 1 本（40 clip × 2 bed = 80 件）。ハーネスの `--external-manifest` へ渡す |
| `fixtures_<level_tag>.yaml` | 水準ごとの content pin（`m2e-external-fixtures/0.1`）。`--external-fixtures` へ渡す |
| `generation_record_<level_tag>.json` | `waveform_sha256` / `factor` / `V` / `B` / `g_b` の記録（§4.5 の `factor` 偏り確認用） |

**生成器が入口で確認すること**（すべて fail-closed）:

- 各ベッド stem の §9.2 canonical decode 後 sha256 が
  `tests/fixtures/melody_bench/m2e_bed_fixtures.yaml` の `expected_stem_sha256` と一致
  すること、かつそのベッドが `accepted: true` であること。
  **破損・差し替え・再取得ミスがあると manifest と fixtures は「間違ったベッド」に対して
  内部整合してしまい、ハーネス側からは検出できない。**
- vocadito の音声・注釈が `m2c_external_fixtures.yaml` の pin と一致すること。
- 出力先が**空**であること（前回実行の残骸と混ざらないため）。

**生成した `fixtures_<tag>.yaml` は測定前に repo へ commit すること。** evaluate の
`_require_attested_external_fixtures_registration` が git 祖先を要求するため、
リポジトリ外に置いたままでは検証段で必ず落ちる。**commit するのは pin ファイルだけで、
波形はリポジトリ外のまま**（`m2c_external_fixtures.yaml` と vocadito 音声の関係と同じ）。

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
    --out <out>/m2e_run_<level_tag>_<repeat>.json \
    --categories V_remix_real_direct V_remix_real_stem \
    --level <+12dB|+6dB|0dB|-6dB> \
    --external-manifest  <out>/manifest_<level_tag>.json \
    --external-fixtures  <out>/fixtures_<level_tag>.yaml \
    --m2e-bars tests/fixtures/melody_bench/m2e_accuracy_bars.yaml \
    --cell-store   <out>/cell_store \
    --repeat-index <0|1>
```

**`--cell-store` / `--repeat-index` は本測定では必須である**（片方だけ渡すと
fail-closed で停止する）。これを省いた 1 回の呼び出しは 1 セルも保存せず、
`B_session` 到達時に**その回の測定が丸ごと失われる**。

**現時点の「1 回」の粒度**（実装の実態を、あるべき姿で上書きしない）:

- 呼び出しの粒度は **(1 水準 × 2 アーム) = 160 セル**である。**シャード選択子は
  まだ無い**（`--categories` と `--level` より細かく切る引数が存在しない）。
  `--workers` は記録専用で、run を並列化しない。
- したがって `m2e_r2_shard_map.yaml`（§8.5）の 1 シャードは、**同じ
  `--cell-store` を渡した再呼び出しの連なり**として実現する。`B_session` で
  打ち切っても完了セルはストアに残り、次回は `env_digest` 一致セルを飛ばして
  続きから走る。**未完セルを「失敗値」で埋めない。**
- 呼び出し 1 回が `B_session = 2.0 時間`。**延長しない。** 超過は異常ではなく
  設計に織り込まれた通常状態（§8.6）。
- 細粒度シャード・evaluate 並列化は **C2 / C3**（rev.6 §8.9.2）で入る。それらが
  landing するまでは上の粒度が実態であり、**r6 は code change を禁じるため
  C2 / C3 は r6 開始前に済ませておく**（§8.9.4）。
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
