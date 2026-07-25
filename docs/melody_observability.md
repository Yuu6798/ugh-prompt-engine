# 主旋律観測センサー M0/M1 — 成立帯域の発見

状態: M0（ベンチマーク基盤）+ M1a/M1b（アダプタ + 観測ゲート）実装完了。
M1c Go/No-Go = **pyin 経路のゲート機構を実測で確立。実利用入力帯（Suno vocals
stem 等）の実測は slow/manual lane へ繰延（machine-dependent）。M2 へ自動進行
しない。**
起点: 設計書「主旋律観測センサー M0/M1（成立帯域の発見）」
規律の根: `docs/learned_models_policy.md`（learned 出力は隔離）、
`docs/recast_phase0_melody_spike.md`（Phase 0 スパイクの負の結果）。

## 0. なぜ再入するか

Phase 0 スパイク（`docs/recast_phase0_melody_spike.md`）は「主旋律保存は測れない」
を示したが、正確には**「明瞭な主旋律を持たない決定論シンセの和音パッドから
pYIN で旋律系列を復元できない」**を測っていた（pYIN が 1–4 音に縮退）。失敗は
比較器（DTW/LCS）ではなく**入力帯域と抽出器の組合せ**にあった。

本トラックは比較を作らない。**「どの入力 × 抽出器なら旋律をそもそも観測できるか」
だけを実測**し、成立帯域を発見する。measure 可能性を確定するまで preserved 判定に
一切進まない。成功条件は「melody preserved を保証すること」ではなく、**「観測できる
条件とできない条件を分離すること」**である。

## 1. モジュール構成

| 資産 | 場所 | 役割 |
|---|---|---|
| 観測ゲート | `src/svp_rpe/melody/observability.py` | ゲート指標算出（M1 本体）。`MelodyObservation`（共通中間表現）→ `MelodyObservabilityReport`。比較を呼ばない |
| 経路選択 | `src/svp_rpe/melody/routing.py` | 入力種別 → 抽出経路候補（設計 §4.2 table 5） |
| 抽出層 | `src/svp_rpe/melody/extractors.py` | 波形 → `MelodyObservation`。pyin（core librosa）+ optional 抽出器へ遅延ディスパッチ |
| Demucs 分離 | `rpe/learned/source_separation_adapter.py` | 既存 `io.source_separator` の vocals stem ラッパ（`SeparatorNotAvailableError`→`LearnedModelUnavailable`） |
| CREPE | `rpe/learned/crepe_adapter.py` | 単旋律 F0（**published extra なし**＝手動 `pip install crepe` の manual/external 統合。コードは MIT だが同梱重みのライセンス未 inspect/未 pin のため extra 非公開。§5） |
| Melodia | `rpe/learned/melodia_adapter.py` | 支配的旋律 F0（**AGPL-3.0**・published extra なし＝手動 `pip install essentia` の manual/external 統合。§5） |
| basic-pitch | `rpe/learned/basic_pitch_adapter.py`（既存） | ノート系抽出器。full_mix 経路の補助 |
| ベンチ基盤 | `tests/fixtures/melody_bench/` | 事前登録レジストリ + 合成仕様 |
| ハーネス | `scripts/run_melody_observability.py` | 全 fixture × 全経路のゲート表を出力 |
| ペア生成 | `scripts/make_melody_pairs.py` | 正解 MIDI 不要の同曲変形ペア（pitch-shift / time-stretch） |

`arrange/observe.py`（D-1 被覆語彙）と `recast/` は**本トラックでは一切変更しない**
（統合は将来の M4）。

## 2. 観測ゲート（M1 本体）

類似度を計算する**前に**、各 (素材 × 抽出器 × 前処理) について旋律データが十分かを
判定し `MelodyObservabilityReport` を返す。基準未達なら `status="insufficient"` を
理由つきで返し、**後段（比較）を呼ばない**。

レポートのフィールド（設計 §4.1）: `voiced_coverage` / `note_count` /
`phrase_count` / `confidence_mean` / `low_confidence_rate` / `octave_jump_rate` /
`cross_extractor_agreement` / `status` / `route` / `reasons`。

抽出器非依存: pyin / CREPE / Melodia はフレーム F0（+ voicing/confidence）を、
basic-pitch はノートを返す。すべて共通中間表現 `MelodyObservation` へ正規化し、
`assess_observability` は抽出器の実装を知らずにゲート指標を算出する。ノート系列が
無ければ `notes_from_frames`（Phase 0 スパイクと同手法: Hz→MIDI・中央値フィルタ・
半音ラン → ノート化）で導出する。

### 事前登録閾値（M0 で固定・M1 で緩めない）

single source of truth = `tests/fixtures/melody_bench/registry.yaml` の
`observation_gate`。根拠は Phase 0 の pyin 縮退（1–4 音）を弾く下限:

| 閾値 | 値 | 意味 |
|---|---|---|
| `min_voiced_coverage` | 0.30 | 有声 F0 フレーム割合の下限 |
| `min_note_count` | 8 | 導出ノート数の下限（和音パッドの 1–4 音を弾く） |
| `min_phrase_count` | 2 | フレーズ数の下限（単一持続音を弾く） |
| `min_confidence_mean` | 0.45 | 有声フレーム平均信頼度の下限 |
| `max_low_confidence_rate` | 0.55 | 低信頼フレーム割合の上限 |
| `max_octave_jump_rate` | 0.35 | オクターブ級ジャンプ割合の上限 |

hold-out 分割（`splits`）で閾値調整用（tuning）と最終確認用（holdout）を分離。
一度 not_observed に落ちた入力帯を係数調整だけで採用に戻さない一方向規則
（`one_way_rule`）を明記。

## 3. 抽出経路（設計 §4.2 table 5）

| 入力種別 | 経路候補 |
|---|---|
| `vocal_track` | pyin_direct / demucs_vocals_then_pyin / **demucs_vocals_then_crepe** / **demucs_vocals_then_melodia** |
| `clear_lead` | pyin_direct / melodia_direct / crepe_direct |
| `full_mix` | demucs_vocals_then_crepe / melodia_direct / basic_pitch_direct（melodia 補助） |
| `chord_pad_no_melody` | not_applicable（`applies=False` → 抽出せず not_observed）+ pyin_negative_control（診断: pyin を当てゲートが insufficient で弾くことを実証） |

入力種別は fixture メタデータで与える（自動判定は M1 の非目標）。

## 4. M1c Go/No-Go 実測記録（2026-07-23）

### 4.1 実測できた層 — pyin 経路（CI 安全・core librosa）

`scripts/run_melody_observability.py`（合成モード）で、`melody_bench` の CI 安全な
合成 fixture 4 種に対し pyin 経路のゲート指標を実測した。正の対照（単旋律）=
`sufficient`、負の対照（和音パッド・ドローン）= `insufficient` が事前期待どおり
分離した:

| fixture | 期待 | 実測 status | notes | phrases | cov | conf | lowconf | oct |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `synth_mono_phrased`（正・tuning） | sufficient | **sufficient** | 15 | 3 | 0.688 | 0.919 | 0.047 | 0.000 |
| `synth_mono_two_phrase`（正・holdout） | sufficient | **sufficient** | 10 | 2 | 0.677 | 0.932 | 0.024 | 0.000 |
| `synth_chord_pad`（負・tuning） | insufficient | **insufficient** | 1 | 1 | 0.955 | 0.485 | 1.000 | 0.000 |
| `synth_unison_drone`（負・holdout） | insufficient | **insufficient** | 1 | 1 | 0.977 | 0.943 | 0.000 | 0.000 |

和音パッドは pyin が 1 音へ縮退（Phase 0 の再現）し `note_count`/`phrase_count`/
`low_confidence_rate` で落ちる。ドローンは高信頼だが単一持続音なので
`note_count`/`phrase_count` で落ちる（信頼度が高くても「旋律」でないことを正しく
not_observed へ）。**ゲート機構が観測可能/不可能を分離することを実測で確認した。**

負の対照は routing 短絡（`not_applicable`）で not_observed に落とすだけでなく、
診断経路 `pyin_negative_control` で pyin を実際に当て、committed ハーネス
（`run_melody_observability.py` 既定・CI 安全）の出力にも `insufficient` として
現れる。これにより「ゲートが抽出器の false positive を弾く」ことが routing 短絡の
陰に隠れず harness 出力で自己実証される（負の対照の本来の役割）。

この結果は `tests/test_melody_observability.py`（`slow` マーカー）が回帰として固定
する。閾値は tuning split の観測のみで M0 に固定し、holdout split
（`synth_mono_two_phrase` / `synth_unison_drone`）は本 Go/No-Go でのみ観測した
（事前期待と一致）。

### 4.2 繰延した層 — 実利用入力帯（machine-dependent）

設計の**本来の標的**は「Suno vocals stem を Demucs→CREPE/Melodia で観測できるか」
である。これは実音源 + 重依存（demucs / crepe / essentia）を要する
**machine-dependent** タスク（CLAUDE.md レビュー振り分けの「実音源」= Codex/User
側）であり、本実装環境には optional 依存が未導入のため**未実測**。ハーネスは経路を
`unavailable` として正直に記録する（fail ではない・slow-lane 隔離）。

実測レシピ（依存導入済みの環境で実行）:

```bash
# 合成 fixture（CI 安全・pyin のみ）
python scripts/run_melody_observability.py --out melody_obs.json

# 実利用入力帯（Suno vocals stem 等の外部素材）
#   ext.json = [{"id": "...", "path": "...wav", "input_kind": "vocal_track"}, ...]
# vocal_track 経路（Demucs vocals → pyin/CREPE）:
pip install -e ".[separate]"      # Demucs（`separate` extra）※重みは事前取得が必要・§6.4
pip install crepe                 # CREPE = manual/external 統合（published extra なし・§5）
# full_mix 経路（登録 fixture real_vocal_plus_backing）は basic_pitch_direct を含む。
# basic-pitch は `pitch` extra（Apache-2.0・Python<3.12）。full_mix も測るなら追加:
pip install -e ".[separate,pitch]"  # + basic-pitch（Python<3.12）
# ※ Melodia を使う場合のみ（AGPL-3.0 を受容できる環境のみ・published extra なし）:
#     pip install essentia   # 手動 install。詳細は §5 ライセンス
# 上記を入れないと該当経路は unavailable として正直に記録される（fail ではない）。
python scripts/run_melody_observability.py --external ext.json --out ext_obs.json
```

正解 MIDI を持たない Suno 素材は**観測可能性のみ**に使い、RPA/RCA（正解精度・M2）
には使わない（設計 §7）。同曲変形ペアは `scripts/make_melody_pairs.py` で
著作権上安全な自作素材から機械生成する。

### 4.3 判定

- **Go（部分）**: pyin 経路の観測ゲートは実装・実測で成立し、合成入力帯で
  観測可能/不可能を分離する。M0 事前登録と M1a/M1b 実装は完了。
- **未確定**: 実利用入力帯（Suno vocals stem）での成立は未実測。設計 §4.4 の
  Go 条件（実用入力帯で事前登録ゲートを安定して満たす経路が 1 本以上）は
  **slow-lane の dated 実測を経るまで判定しない**。設計 §7・§4.4 に従い
  **M2 へ自動進行しない**（明示的 Go 判定を要求）。全経路 insufficient を確認した
  わけでもないので「強化版 not_observed」も宣言しない。現状は「基盤 + pyin 経路
  確立、実標的計測は machine-dependent で繰延」である。

## 5. 規律（設計 §5 準拠）

- **slow-lane 隔離**: Demucs は optional extra（`separate`）、basic-pitch は
  `pitch` extra。CREPE と Melodia は下記ライセンス理由で published extra を持たず
  manual/external 統合。標準 CI は重依存なしで green（ハーネス既定の pyin 経路は
  core librosa のみ）。
- **learned 隔離**: 抽出結果を決定論 RPE（`PhysicalRPE.melody_contour` 等）へ
  混ぜない。本トラックは観測レポート（`MelodyObservabilityReport`）のみを返す。
- **ライセンス**: basic-pitch=Apache-2.0（`pitch` extra）/ Demucs=`separate` extra。
  **CREPE** はコードこそ MIT だが同梱重み（model-*.h5）のライセンスを別途 inspect
  して pin していないため（policy §4「permissive なコードは permissive な重みを含意
  しない」）、published extra を用意せず manual/external 統合とする（手動
  `pip install crepe`・`weights_license` は「未検証」を fail-closed で明示）。重み
  ライセンスの実確認と pin は machine-dependent な slow-lane 課題で、確認後に
  policy へ記録した上で extra 昇格を検討する。**Essentia(Melodia)=AGPL-3.0** はコピーレフトで、
  `docs/learned_models_policy.md` の「runtime 依存は permissive のみ」方針が
  例外を認めていない。よって **published extra は用意せず**（`[melodia]` extra は
  存在しない）、AGPL を受容するユーザが slow/manual lane で `pip install essentia`
  を手動実行したときだけアダプタが拾う external 統合とする（advertised 依存面を
  canonical policy と矛盾させない。first-class extra への昇格は policy への
  approved exception 記載が前提）。
- **事前登録厳守 / not_observed の正直さ**: 旋律不在・被覆不足・抽出器不一致は
  全て not_observed（判断不能であって保存されたではない）。preserved と偽称しない。

### 実測環境（attestation・2026-07-23）

| package | version |
|---|---|
| python | 3.11.15 |
| numpy | 2.4.6 |
| scipy | 1.17.1 |
| librosa | 0.11.0 |
| soundfile | 0.14.0 |
| numba | 0.66.0 |
| pydantic | 2.13.4 |

§4.1 の数値は上記環境の pyin 経路実測。異バージョン環境での再実行は本記録の検証
範囲外であり、差異が出ても「壊れた」ではなく新しい日付の再実測として記録する。

## 6. M1-real 事前登録（実測待ち・2026-07-24）

§4.2 で繰延した実利用入力帯（Suno vocals stem 相当の実ボーカル曲）の slow-lane
実測に向け、**測定を見る前に**合否バーと素材を凍結した（`one_way_rule` と同型の
規律）。本節はワイヤリングと事前登録のみを記録する — 測定値・Go/No-Go 判定は
**含まない**（machine-dependent・slow/manual lane へ繰延）。

### 6.1 凍結した Go bar

`tests/fixtures/melody_bench/registry.yaml` の `m1_real_go_bar`（`registered_utc:
"2026-07-24"`）:

- positive 4 本中 **≥3 本**で `status=sufficient` を返す経路が 1 本以上存在
  （`min_positive_sufficient: 3` / `total_positive: 4`）
- その経路が全 negative で **偽陽性ゼロ**（`max_negative_false_positive: 0`）
- 各素材×経路は **n≥2** 回実行して確認（`repeats_min: 2`。決定論抽出器なら一致する
  はずで、実行揺れの有無自体も記録に値する）

`external_fixtures` に登録した M1-real 素材（`category: real_no_truth`、全て
`input_kind: vocal_track`）:

| id | source | 役割 |
|---|---|---|
| `real_vocal_jrock` | kane_y2 | positive |
| `real_vocal_futurepop` | sev4_x2 | positive |
| `real_vocal_band` | kaze_k2 | positive |
| `real_vocal_waltz` | crslv2_w3 | positive（3拍子・器楽厚めの難所） |
| `real_instrumental_negative` | （自作曲の器楽区間） | negative（誤検出ガード） |

判定は `scripts/run_melody_observability.py` の `evaluate_m1_real_go_bar` が
この凍結バーから機械算出する（目視で緩めない）。

### 6.2 vocal_song → vocal_track 写像

設計上の `vocal_song` ラベルは `src/svp_rpe/melody/routing.py` の `INPUT_KINDS`
（`vocal_track` / `clear_lead` / `full_mix` / `chord_pad_no_melody`）に存在しない。
5 本すべてを `vocal_track` へ写像した。§3 の経路表のとおり、`vocal_track` は
`demucs_vocals_then_crepe` と `demucs_vocals_then_melodia` の**両方**（§3.2 の
本命経路）を持つ唯一のバケットであり、設計の狙い（Demucs 分離後の単旋律抽出器）
と一致する。

### 6.3 走らせるべき経路行列

`vocal_track` の経路候補（§3 table 再掲）: `pyin_direct`（baseline sep 無し）/
`demucs_vocals_then_pyin`（sep→pyin）/ `demucs_vocals_then_crepe`（sep→crepe・
本命）/ `demucs_vocals_then_melodia`（sep→melodia・本命）。設計 §3.2 に準拠し、
この 4 経路すべてを 5 素材（positive 4 + negative 1）× n≥2 で回す。

### 6.4 slow-lane 実行レシピ（Cowork/User が実行）

```bash
# pitch 依存（pyin baseline は追加依存なしで動く。crepe/melodia は §5 manual）
pip install -e ".[pitch]"
# pip install crepe        # CREPE（manual/external 統合・§5）
# pip install essentia     # Melodia を測るなら（AGPL-3.0 を受容できる環境のみ）

# ★分離経路（本命 demucs_vocals_then_*）を測るなら `.[separate]` を入れる。
#   ハーネスは分離が走った行に preprocessing.stem_sha256 /
#   separation_weights_sha256 を emit し、学習抽出器行に extractor_weights_sha256 を
#   emit するので、評価器の #54/#59 要求と噛み合う（旧レシピの「入れるな」回避は撤去）。
pip install -e ".[separate]"

# ★重みは**事前取得**する（ハーネスは実行時 DL を一切行わない）。未取得のまま走らせても
#   CDN へは触れず、分離経路が unavailable として記録され run は完走する（→ inconclusive）。
#   取得方法 A: 明示的な一度きりの provisioning（download はこのコマンドの中だけで起きる）
python -c "import demucs.pretrained as p; p.get_model('htdemucs_ft')"
#   取得方法 B: 遮断環境では別マシンで取得した .th を
#              ~/.cache/torch/hub/checkpoints/ へ配置する。別の置き場所にしたい場合は
#              torch ネイティブの TORCH_HOME=<dir> を設定する（demucs も本ゲートも
#              同じ規約で解決するので、pin する重みと demucs が読む重みが必ず一致する。
#              独自の探索パスは意図的に持たない — 別ディレクトリを hash しつつ demucs が
#              既定 cache から読む乖離を作らないため）
#
#   取得できたことの確認と、registry へ記録する dated pin の採取:
python -c "import json; from svp_rpe.rpe.learned.source_separation_adapter import \
  describe_separation_weights as d; print(json.dumps(d(), indent=2, ensure_ascii=False))"
#   → {"model": "htdemucs_ft", "version": "...", "sha256": "...", "files": [...]}
#   sha256 は checkpoint（.th）だけでなく **bag 定義 YAML**（htdemucs_ft.yaml）も覆う。
#   bag YAML は signature 選択・per-source weights・segment を持つ実行時のモデル入力で、
#   同じ .th でも構成が違えば別の stem が出るため（Codex #217）。
#   この version / sha256 を registry.yaml の provenance.model_weights.demucs へ記録する
#   （crepe / melodia / basic_pitch の重み pin は run 出力 report の
#    routes[].extractor_weights_sha256）。★記録した pin は飾りではない: 評価器は
#    **記録済み（非 null）の sha256 と version の双方について report 行の値との一致を
#    必須化**し、食い違えば fail-closed で Go を出さない（#217。sha256=同じ bytes か、
#    version=同じ実装リリースか）。未記録（null）の間は要求しないので、記録前の run は
#    これまでどおり進む。

# tests/fixtures/melody_bench/external_manifest.example.json をコピーし、
# REPLACE を実ファイルパスへ書き換える（audio_sha256 は null のままでよい・
# ハーネスが実測して記録する）。

# n>=2 回、それぞれ別ファイルへ観測表を書き出す（run_id はハーネスが自動発行）。
python scripts/run_melody_observability.py --external manifest.json --out run1.json
python scripts/run_melody_observability.py --external manifest.json --out run2.json

# ★凍結素材 audio pin の記録（#53。評価器は Go 判定 publish に必須）。run1.json 等の
#   fixtures.<id>.audio_sha256 は run 出力に実測記録される。これを registry.yaml の
#   external_fixtures[].expected_audio_sha256 へ転記して registry を更新し、（registry が
#   変わるので）上の run1/run2 生成を更新後 registry で**やり直す**。この記録前に
#   --evaluate-go-bar を回すと expected_audio_sha256 欠落で fail-closed する。
#   ※ 生成と評価は同一 checkout で行う（#55: report の generator_code_sha256 は現 checkout の
#     _generator_code_sha256() と一致必須。間で routing/gate/extractor を変えると stale 扱い）。

# 凍結バーを機械適用して Go/No-Go を得る（重み事前取得済みなら分離経路も採点対象）。
python scripts/run_melody_observability.py --evaluate-go-bar run1.json run2.json --out verdict.json
```

**重みプロビジョニングの 3 状態**（可用性は import 可否の 2 値ではない）:

| 状態 | 挙動 |
|---|---|
| demucs 未導入 | 分離経路 = `unavailable`（`separate` extra の install hint） |
| 導入済 + 重み取得済 | 分離経路が measured。stem/weights pin つきで Go 候補になれる |
| 導入済 + **重み未取得** | 分離経路 = `unavailable`（`demucs weights not provisioned: <expected paths>`）。**実行時 DL は行わない**（リトライもミラーもしない）ので run は完走し、部分行と report は残る |

**メタデータのスナップショット**: `files.txt` / bag YAML は選択（どの `.th` を読むか）と
pin（何を hash するか）の両方に効くので、**1 回の read で確定**させ、parse も digest も
同じ bytes から作る（別々に read すると、その間の差し替えで「旧選択を検証しつつ新メタデータで
分離する」ズレが生じる）。残る限界: demucs 自身に本スナップショットを消費させることは
できない（`repo=` 経由にすると「実際に読む場所以外を参照する」構図に戻るため採らない）ので、
実行中にパッケージを書き換えられた場合の検出は分離後の再解決（→ `unavailable`）による
事後検出になる。重みの provisioning / パッケージ更新と実測 run は同時に走らせないこと。

検査するのは **demucs が実際に読む場所**（torch hub の checkpoints cache）だけで、
独自の探索パスは持たない。別ディレクトリを探索して hash する設計にすると、demucs は
既定 cache から読み、`separation_weights_sha256` は stem を作っていない重みを指す
——「pin とモデル入力の乖離」が生じるため（Codex #217 指摘）。置き場所を変えるなら
`TORCH_HOME` を使う（torch/demucs と本ゲートが同じ規約で解決する）。

正確には、探索先は `separate_stems` がどちらの経路で分離するかで決まる:

- **API 経路（プロセス内分離。`demucs.api` が import できる）**: **active hub dir**
  （`torch.hub.get_dir()`）だけを見る。`torch.hub.set_dir()` を呼んだプロセスでは
  `TORCH_HOME` と食い違いうるため、env 由来のパスを併記すると demucs が読まない
  ファイルを hash しうる。
- **CLI 経路（`python -m demucs` の子プロセスへフォールバック）**: 子は `os.environ` を
  継承するだけで親の `torch.hub.set_dir()` は届かないため、**env 由来の解決**
  （`TORCH_HOME` → `XDG_CACHE_HOME` → `~/.cache`）を見る。ここで親の active hub dir を
  見ると、子が実際に読む cache と pin が乖離する。

torch を import できないときも env 由来へ落ちる（重み未取得判定に torch の import を
必須にしないため）。重みが未取得のときのエラーメッセージには、どちらの経路で解決したかも
出る。

**実行中の artifact 差し替え**（TOCTOU）: hash を採ってからモデルが独立に同じファイルを
開くまでの間に、別プロセスが provisioning / 更新 / 差し替えを行うと、pin は旧 bytes を
指しつつ成果物は新 bytes 由来になりうる。分離（Demucs）でも抽出器（CREPE / Melodia /
basic-pitch）でも **推論前に指紋を採り、推論後に memo を迂回して再検証**し、食い違えば
**成果物を返さず `unavailable`** にする（対応しない pin を publish するより測定未達の方が
正しい）。モデルの provisioning / 更新と実測 run は同時に走らせないこと。関連して:

- **選択集合の再解決**: 分離側は checkpoint を再 hash するだけでなく、`files.txt` と
  bag YAML を pin 対象に含めた上で**分離後に集合を解決し直して比較**する。メタデータが
  差し替わると「どの `.th` を読むか」自体が変わり、旧集合を再 hash するだけでは
  「別集合で分離したのに pin は旧集合」を見逃すため。
- **in-memory model cache**: CREPE 等はロード済みモデルをプロセス global に cache する。
  初回ロード後に artifact が差し替わると、ディスクの pre/post は新 bytes で一致するのに
  推論は旧モデルのまま、という状態がありうる。プロセス内の **load-time pin**（最初に
  観測した digest）を**推論前に bind**し、食い違えば**推論そのものを行わず** `unavailable`
  にする（推論後に pin を初期化すると、旧モデルが生んだ観測へ新 digest が付く）。
  残る限界: **本経路を通らずに**モデルがロードされていた場合、そのロード時点の digest は
  原理的に知りようがない。ハーネスは抽出器を本経路からしか呼ばないので、slow-lane は
  **1 run = 1 プロセス**で回すこと（レシピどおり run1/run2 を別コマンドで実行すれば満たす）。

なお、これらの解決・hash 段で起きる想定外の失敗（壊れた cache・不正な bag YAML・
discovery 後に読めなくなったファイル）も `LearnedModelUnavailable` へ写像する。
`_run_routes_on_file` は `LearnedModelUnavailable` だけを catch して route を
`unavailable` に落とすので、素の `OSError` / `yaml.YAMLError` が貫通すると
**run 全体が落ちて部分行も report も残らない**（D-1 が塞いだ失敗形の再来）。

第 3 状態を「利用可能」と誤認すると、遮断環境では torch hub の download が
`urllib.error.URLError` を投げて `--external` run 全体が落ち、部分行も report も
残らなかった（旧レシピが `.[separate]` を入れるなと書いていたのはこの穴の回避策）。
現在は `ensure_separation_available()` が **重みのローカル実在まで**検査し、
分離実行前に必ずこの門を通す。門をすり抜けた重み取得起因の失敗
（`URLError` / `HTTPError` / `OSError` / torch hub の `RuntimeError`）も
`LearnedModelUnavailable` へ写像するので、run が落ちることはない。

**pyin_direct だけで Go が出た場合の扱い**: 分離なし full-mix の pyin は Phase 0
スパイクと 2026-07-24 のスモーク実測（`voiced_coverage 0.181 / confidence 0.023`）の
両方で不成立側の証拠がある。したがって pyin_direct 単独の Go は M1-real の本Go として
扱わず、**それ自体が驚くべき結果**として dated 記録し、素材・区間を疑って再確認する。
逆に、分離経路が `unavailable` で verdict が `inconclusive` になったものを「No-Go」と
読み替えてはならない（測定未達を測定結果と偽らない）。

`verdict` は三値: **`go`**（生き残り経路あり）/ **`no_go`**（**全**候補経路を全
fixture×route で実測した上でどれも生き残らなかった＝強化版 No-Go・設計 §4.4）/
**`inconclusive`**（1 つでも未測定の候補経路がある＝optional 依存欠如で本命経路が
`unavailable` 等・「生き残りなし」を証明できておらず「測定未達」と偽らない）。
`--evaluate-go-bar` は入力 report を凍結 registry と多重に照合し（registry_sha256 /
observation_gate 一致・凍結 kind と route matrix・素材別性・model provenance・繰返し
独立性）、いずれかが破れれば fail-closed で verdict を出さない。`verdict.json` は消費した
report の `report_pins` を記録する。

決定論パイプラインでは同一素材の抽出は bit 単位で同一結果を返すため、n≥2 の repeats が
意味を持つのは**独立した実行**の揺れを見るときだけ（設計 §3.1）。そこで `--external`
実行は毎回新規の **`run_id`** を発行し、評価器は全 report が非空 `run_id` を持ち相互に
distinct であることを要求する。これがないと run1.json を run2.json へコピーするだけで
別パス・同一 bytes として path-dedup を通過し、1 回の抽出が `repeats_min=2` を満たして
しまう（`audio_sha256` の一致＝同一素材とは直交する軸で、素材は同一・実行は独立を要求）。
`verdict.json` は消費した `run_ids` も転記する。

report/manifest の JSON は**重複 object キーを拒否する hook**（`object_pairs_hook`）で
parse する。標準 `json.loads` は重複キーを last-wins で黙って畳むため、失敗版→合格版の
2 重 `fixtures.<id>` を持つ stale/手書き report は、矛盾する bytes を content hash で
pin しつつ合格版だけ採点して go を publish しうる。hook は全ネスト階層の object で
呼ばれるので、fixture id・route payload いずれの階層の重複キーも採点前に fail-closed で
弾く（#46）。

report は route 行・gate metrics を産出した generator コードの digest を
**`generator_code_sha256`** に載せる。評価器はこれが全 report に存在し repeats 間で一致
することを要求する。verdict の `evaluator_code_sha256`（判定コードの digest）と対を成す
生成側 provenance で、extractor/gate コードが変わった後に古い report bytes が渡される
stale extraction を機械検出可能にする（従来は registry と評価器コードしか pin されず
検出不能だった）。同じパス集合（`_generator_code_paths()`）が `--out` 衝突保護でも使われ、
生成直後の generator コード上書き破壊を両モードで防ぐ（#47/#49/#50/#51）。

この generator コード集合は**ハードコードのモジュール一覧ではなく、seed（harness ＋ melody
抽出/経路/観測 ＋ learned adapter / source separator）から AST で import を辿った first-party
推移閉包**として算出する。`ast.walk` は関数内 import も拾うため、pyin baseline が遅延 import
する `rpe.physical_features` の `PYIN_*` 定数・`_highpass_melody_signal` のような、hand-list が
取りこぼしがちな依存も自動で含む（#52）。`test_generator_code_paths_is_import_closed` が
「集合が import 閉包として閉じている」不変条件を CI で守り、将来 generator 系に遅延 import を
足して集合が不完全化しても検出する。

`report_pins` の **`sha256` が content-addressed の replay anchor** で、各 report の
内容を一意に pin する（同名 basename でも内容が違えば別 sha256 として list に共存・
区別される）。`path` は人間可読の**非権威的 hint** に徹する（repo 内なら repo 相対、
repo 外なら basename）——slow-lane report は machine-dependent な transient artifact
（§6.5・commit しない）で検証時には存在しないため、provenance の同一性は path でなく
sha256 で担保する。検証は「pin した sha256 の report 集合を同一凍結 registry の下で
再評価して同じ verdict を得る」ことで行い、path で元ファイルを open して replay する
ものではない。

### 6.5 状態

本節はワイヤリング + 事前登録のみ。実音声 + Demucs/CREPE/Essentia を要する
実測は **machine-dependent** であり slow/manual lane（Cowork/User）へ繰延する。
測定値・dated 実測記録・Go/No-Go 判定は本節時点では **未確定（PENDING）** —
実測が済むまでここに数値や verdict を書き加えてはならない（一方向規律）。

**繰延している machine-dependent 課題**（値の **emit/記録** が machine-dependent。ただし
評価器の**要求は machine-independent** で、Go 判定を publish する scoring 時点でこれらの
pin を必須化する = 記録が済むまで Go を出さない fail-closed 規律。「約束するのは測定できる
ものだけ」の D-1 準拠）:

- **分離経路の stem/weights hash**（#54）: **emit 配線済み**（2026-07-24）。分離が実際に
  走った行には `preprocessing.stem_sha256`（vocals stem の float32 生サンプル hash）と
  `separation_weights_sha256`（実際に読んだチェックポイントの hash）が載る
  （`isolate_vocals_with_provenance` → `observe_via_route_with_provenance` → 行）。
  評価器の必須要求（同一 `htdemucs_ft`/version でも別 weights/再生成 stem なら前処理入力が
  変わる）はそのままで、**要求を満たす値を実測時に刻めるようになった**のが変更点。
  実際の値は machine-dependent（実 Demucs + 実素材）なので依然 slow-lane で採る。
- **学習抽出器の weights hash**（#59）: **emit 配線済み**（2026-07-24）。CREPE は
  `crepe/model-<capacity>.h5`、basic-pitch は `ICASSP_2022_MODEL_PATH` の artifact を
  hash して `extractor_weights_sha256`（`extractor_weights_kind: model_weights`）に載せる。
  Melodia は**学習重みを持たない DSP 算法**なので、pin するのは essentia のネイティブ
  拡張バイナリで、`extractor_weights_kind: library_binary` として「重みでないものを重みと
  主張しない」正直会計にする。pyin は DSP で重みなしのため無記入（評価器も要求しない）。
  依存未導入・artifact 未特定のときは推測 digest を作らない。**artifact を持つ抽出器**
  （CREPE / basic-pitch / Melodia）では指紋を採れないこと自体が provisioning 失敗なので、
  推論へ進まず当該 route を `unavailable` にする（そのまま進むと、生の I/O 例外で run
  全体が落ちるか、評価器が要求する hash を欠いた measured 行が出て Go-bar 評価が丸ごと
  fail-closed する）。pyin は artifact を持たないので従来どおり無記入で観測する。
- **推論コードの pin**（`extractor_code_sha256` / `preprocessing.separation_code_sha256`）:
  重み hash と distribution version は「同じ bytes か / 同じリリースか」しか保証せず、
  **同一 version のままローカル patch / repack された**パッケージは素通りする
  （`generator_code_sha256` は first-party しか覆わない）。実際に推論した third-party
  パッケージの `.py` + ネイティブ拡張を hash して行に載せ、provenance 署名（repeats 一致）に
  含める。評価器は **measured 行に推論コード pin を必須**とする（主・assist・分離の
  それぞれ。registry の `code_sha256` が未記録でも要求する — 要求しないと、手書き report が
  pin を削っても「両方欠落 = 一致」として通り、推論コード未 pin のまま go を publish できる）。
  registry に `code_sha256` を記録した場合は行との一致も追加で必須化する。
  **実行 backend も覆う**（CREPE / basic-pitch は TensorFlow がモデルグラフを実行し、
  Demucs は torch が実行するため、抽出器パッケージだけでは patch を検出できない）:
  crepe→`crepe`+`tensorflow`/`keras`、basic_pitch→`basic_pitch`+TF/ONNX/CoreML/TFLite、
  melodia→`essentia`（ネイティブ実装が算法そのもの）、pyin→`librosa`+`scipy`、
  入力は **pin 済みの soundfile スタックで読めるものだけ**を観測する
  （`librosa.load` / `get_duration` は soundfile が開けない入力で audioread＝裏は
  未 pin の FFmpeg / GStreamer へフォールバックするため、入口で `sf.info` を通して
  fail-closed にする。尺＝被覆の分母も audioread 由来の値を採らない）。
  加えて **`soundfile` は全抽出器の閉包に入る**（`librosa.load` は WAV/FLAC を
  soundfile 経由でデコードし、合成経路は `sf.write` で観測対象そのものを書く。
  デコードの実体は **libsndfile ネイティブ共有ライブラリ**なので、単一モジュール
  配布の同梱物 `_soundfile.py` / `_soundfile_data/` まで hash 対象に含める。
  単一モジュール（site-packages 直下の 1 ファイル）は親ディレクトリを rglob すると
  site-packages 全体を巻き込むため、パッケージとは別扱いにする。
  **同梱ネイティブを持たない install 形態**（distro / source ビルド）は
  システムの libsndfile を読むので、`ctypes.util.find_library` +
  ローダと同じ探索順（`LD_LIBRARY_PATH` → `ldconfig -p` キャッシュ）で実体パスを
  解決して hash する。**解決に dlopen を使わない**のが要点 — ロードすると `CDLL` を
  捨てても mapping はプロセスに残り、直後にファイルが in-place で書き換えられた場合
  「実行はロード済みの旧コード / 指紋は新 bytes」になり、その観測を生んでいない
  コードの pin が付く。解決できなければ wrapper だけの pin を publish せず
  fail-closed）、
  **分離は `ffmpeg` / `ffprobe` の実行ファイルも同じ digest に畳む**
  （CLI（`python -m demucs`）も API（`Separator.separate_audio_file()` の `AudioFile`
  読み出し）も外部 FFmpeg を叩くため経路で区別しない。別ビルドの FFmpeg は分離へ入る
  波形を変えるのに demucs/torch の pin も weights pin も動かない。PATH 不在時の扱いだけ
  経路で分かれる: CLI は `_demucs_subprocess_env()` が実在を必須にするので fail-closed、
  API は別デコーダへフォールバックし**その実行ファイルは結果に影響しえない**ので covered
  から外して続行する＝実行されていないものを pin したことにしない。CLI/API の判定は
  demucs を import せずに行い、判定が外れても分離後の再 hash が実測値で計算されるため
  before/after 比較が拾う）。**FFmpeg は実行ファイルだけでなくデコード実装の共有
  ライブラリ（`libavformat` / `libavcodec` / `libswresample` 等）も pin する** —
  distro 版は実装がそちらにあるため。解決は ELF の `DT_NEEDED` を読んで推移的に行い
  （`ldd` は対象を実行しうるので使わない）、探索は glibc ローダと同じ順
  （`DT_RPATH`（`DT_RUNPATH` が無いときのみ）→ `LD_LIBRARY_PATH` → `DT_RUNPATH` →
  `ldconfig` キャッシュ。展開するのは `$ORIGIN`（参照元オブジェクトのディレクトリ）
  だけで、**`$LIB` / `$PLATFORM` はローダが決める値**（Debian の `lib/x86_64-linux-gnu` /
  `haswell` 等）なので推測せず fail-closed——展開できない候補を飛ばして ldconfig に
  落ちると同名のシステムライブラリを掴む。`DT_RPATH` は `DT_RUNPATH` と違い
  **依存の依存にも継承される**ので、closure は祖先の RPATH を引き継いで解決する）で行う
  ——conda / アプリ同梱ビルドは同名の `libav*` を同梱位置から読むため、これを見ないと
  「同名のシステムライブラリを hash したが、デコードしたのは同梱版」になる。
  線は **FFmpeg 自身のライブラリ**まで
  （libc/libm 等の OS 基盤まで広げると「環境全体が推論スタック」になり誰も守れない）。
  依存の解決は**プログラムヘッダの `PT_DYNAMIC`** を読む（強く strip された
  バイナリはセクションヘッダを持たないが、ローダは `PT_DYNAMIC` で解決するため、
  セクションが無いことを「静的リンク」の証拠にしない。静的の確証は `PT_DYNAMIC` が
  存在しないこと）。静的リンク（依存が無いことを**読んで確認**できる ELF）では
  closure は空。一方
  **非 ELF**（Mach-O / PE / ラッパスクリプト）は closure を読めないので fail-closed
  ——「読めなかった」を「依存なし」と主張しない。結果として分離経路の pin は現状
  **Linux/ELF 限定**で成立する、
  CREPE は既定の `viterbi=True` が **`hmmlearn`** の HMM デコードで F0 系列の選択を
  決めるので閉包に含める、**librosa 経由で必ず実行される数値バックエンド
  （`soxr` / `numba` / `llvmlite`）も全閉包に入れる**（`librosa.resample` の既定
  `res_type="soxr_hq"` は SoXR のネイティブ実装がリサンプルそのもの、librosa の JIT
  カーネル（`librosa.sequence.viterbi` 等）と resampy のリサンプルカーネルは
  numba/llvmlite がコンパイルして実行する）、
  分離→`demucs`+`torch`。加えて **`librosa` は全抽出器の閉包に入る** — 本アダプタ層が
  非分離経路の波形 decode・Melodia 入力のリサンプル・basic-pitch の被覆分母となる実尺取得に
  librosa を使うため、patch された librosa は抽出器へ渡る波形やゲート指標を変えるのに
  source audio hash も抽出器 pin も version も動かない。numpy/scipy は汎用数値基盤として
  線の外に置くのが原則だったが、**本層のコードが直接呼ぶ以上どちらも閉包に入れる** —
  pyin は `_highpass_melody_signal`（`scipy.signal.butter` / `sosfiltfilt`）で前処理して
  から `librosa.pyin` へ渡し、`extractors.py` は `asarray` / `isfinite` / `where` /
  `nan_to_num` で観測値そのものを組み立て、分離側も stem を numpy で正規化する。
  patch された scipy / numpy は観測とゲート指標を変えるのに、抽出器 pin も weights pin も
  version も動かない。numpy を bind より前に import しないため、`utils/hashing.py` の
  numpy は関数内 import に落としてある（provenance の import 閉包は numpy を引かない）。
  パッケージ走査は `.py` / `.so` / `.pyd` / `.dylib` に加え、**版番号付き `lib*.so.1` と
  Windows の `.dll`** も拾う（TensorFlow / PyTorch のバックエンドライブラリはこの形）。import できなかった backend は飛ばし、**実際に覆った名前**を
  `extractor_code_packages` / `separation_code_packages` に列挙する（被覆の正直会計）。
  コード hash の解決は `importlib.util.find_spec` で**モジュールを実行せず**場所だけを
  引くので、bind を**当該パッケージの import より前**に置ける。ハーネスは
  `bind_inference_code_pins()` を **モジュール本体の import 列より前**（`soundfile` /
  `build_melody_bench` / `melody.extractors` を引く前）で呼び、run の入口でも呼ぶ。
  これで `_generator_code_sha256()` の閉包探索（`io.source_separator` 経由で demucs と
  soundfile を import する）より前に全 pin が固定される。route 内でも、artifact 解決
  （third-party を import する）より**前**にコード pin を bind する。import 後に hash すると「cache 済みの旧コードが実行され、
  hash は新ファイルを見る」窓が開くため。残る限界: 本経路より前に**別の経路や別トラックが
  同じパッケージを import 済み**なら、その時点の digest は知りようがない（load-time pin と
  同じ制約で、slow-lane は 1 run = 1 プロセスで回すことで満たす）。
  コード pin も weights と同様に**推論前に bind → 推論後に memo 迂回で再検証**する
  （import 済みモジュールはプロセスに cache され、途中で差し替えても実行は旧コードのまま
  でありうるため）。**分離（Demucs）側も同じ**で、分離前に bind し分離後に再検証する。
  推論を行うパッケージのコード hash を**採れない**場合（zip/namespace レイアウト、
  ロード済みファイルが読めない等）は、無記入で measured 行を出さず `unavailable` にする。
  `find_spec` 自体が例外を投げた場合（`sys.modules` にあるが `__spec__` 欠落 =`ValueError`、
  meta path finder の失敗等）も `unhashable` として fail-closed に倒す — top-level 名は
  **未導入なら `None` が返る**ので、例外は「導入されているかもしれないのに解決できない」
  を意味し、absent として skip すると実行されうる実装を覆わない pin を publish しうる。
  **部分被覆も同様**: 未導入の optional backend（`absent`）は「実行されていない」ので
  飛ばしてよいが、**導入済み（= 実行されうる）なのに hash できない**（`unhashable`）
  パッケージがあれば、他だけで digest を作らず fail-closed にする（実行された実装の一部を
  覆わない pin を「揃っている」と誤認しないため）
  —— 評価器は measured 行に code pin を必須とするので、無記入の行は Go-bar 評価で
  fail-closed になる（route 単位で `unavailable` に落とす方が、report 全体を弾くより正しい）。**assist 抽出器**（full_mix の `basic_pitch_direct` × Melodia など）も
  同様に `assist_extractor_weights_sha256` を emit する — `cross_extractor_agreement` は
  assist のモデル入力に依存する gate metric なので、主抽出器と同じく pin する（Codex #217）。
  評価器は `assist_status == "measured"` の行にこの pin を必須とし、provenance 署名にも
  含める（assist が `unavailable` の行は agreement が null なので要求しない）。
- **frozen 素材の expected audio hash**（#53）: real_vocal_* は自作 Suno 曲で **非 commit**
  （波形は repo に置かない）、その expected audio sha256 は slow-lane 生成時に決まる
  dated pin。PR 時に registry へ固定できない（audio が repo に存在しない・初回生成前は
  hash 未知）。評価器は **全 go-bar fixture に registry の `expected_audio_sha256` を必須**とし、
  report の audio_sha256 と一致要求する（未記録だと manifest が frozen id を誤った audio に
  向けても両 repeats で一致してしまい、一度も pin されていない material に Go が出る）。
  operator が初回生成後に実測 hash を registry へ記録してからでないと scoring で Go を出さない。
