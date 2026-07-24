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
pip install -e ".[separate]"      # Demucs（`separate` extra）
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
# 重依存導入（Melodia を含めるなら essentia は AGPL-3.0・標準 CI/install には含めない）
pip install -e ".[separate,pitch]"
# pip install crepe        # CREPE（manual/external 統合・§5）
# pip install essentia     # Melodia を測るなら（AGPL-3.0 を受容できる環境のみ）

# tests/fixtures/melody_bench/external_manifest.example.json をコピーし、
# REPLACE を実ファイルパスへ書き換える（audio_sha256 は null のままでよい・
# ハーネスが実測して記録する）。

# n>=2 回、それぞれ別ファイルへ観測表を書き出す。
python scripts/run_melody_observability.py --external manifest.json --out run1.json
python scripts/run_melody_observability.py --external manifest.json --out run2.json

# 凍結バーを機械適用して Go/No-Go を得る。
python scripts/run_melody_observability.py --evaluate-go-bar run1.json run2.json --out verdict.json
```

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

report は route 行・gate metrics を産出した generator コード（harness ＋ melody
抽出/経路/観測 ＋ 下流 learned adapter / source separator）の digest を
**`generator_code_sha256`** に載せる。評価器はこれが全 report に存在し repeats 間で一致
することを要求する。verdict の `evaluator_code_sha256`（判定コードの digest）と対を成す
生成側 provenance で、extractor/gate コードが変わった後に古い report bytes が渡される
stale extraction を機械検出可能にする（従来は registry と評価器コードしか pin されず
検出不能だった）。同じパス集合（`_generator_code_paths()`）が `--out` 衝突保護でも使われ、
生成直後の generator コード上書き破壊を両モードで防ぐ（#47/#49/#50/#51）。

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

**繰延している machine-dependent 課題**（いずれも評価器は forward-compat 済みで、
slow-lane 実測時に値が記録されれば自動で穴が閉じる。値の **emit/記録** が machine-dependent）:

- **分離経路の stem/weights hash**: Demucs vocals stem の sha256・分離器重みの hash は
  実 Demucs を要するため未 emit。評価器 `_route_provenance` は
  `preprocessing.stem_sha256` / `separation_weights_sha256` が**存在すれば** repeats 間で
  比較する。emit 配線（`_preprocessing_provenance` が stem を露出して hash）は実測時に追加。
- **frozen 素材の expected audio hash**: real_vocal_* は自作 Suno 曲で **非 commit**
  （波形は repo に置かない）、その expected audio sha256 は slow-lane 生成時に決まる
  dated pin。PR 時に registry へ固定できない（audio が repo に存在しない・初回生成前は
  hash 未知）。評価器は registry の external_fixtures エントリに `expected_audio_sha256`
  が**存在すれば** report の audio_sha256 と一致要求する。operator が初回生成後に
  実測 hash を registry へ記録すれば、manifest typo/差替で別素材に verdict を出す穴が閉じる。
