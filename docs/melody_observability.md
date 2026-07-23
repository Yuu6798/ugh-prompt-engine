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
| CREPE | `rpe/learned/crepe_adapter.py` | 単旋律 F0（optional `crepe` extra・MIT） |
| Melodia | `rpe/learned/melodia_adapter.py` | 支配的旋律 F0（optional `melodia` extra・**AGPL-3.0**） |
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
| `chord_pad_no_melody` | not_applicable（`applies=False` → 抽出せず not_observed） |

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
pip install -e ".[separate,crepe]"       # Demucs + CREPE（MIT/Apache 系）
# ※ Melodia を使う場合のみ（AGPL-3.0 を受容できる環境のみ・published extra なし）:
#     pip install essentia   # 手動 install。詳細は §5 ライセンス
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

- **slow-lane 隔離**: Demucs/CREPE は optional extra（`separate` / `crepe`）。
  Melodia は下記ライセンス理由で published extra を持たず manual/external 統合。
  標準 CI は重依存なしで green（ハーネス既定の pyin 経路は core librosa のみ）。
- **learned 隔離**: 抽出結果を決定論 RPE（`PhysicalRPE.melody_contour` 等）へ
  混ぜない。本トラックは観測レポート（`MelodyObservabilityReport`）のみを返す。
- **ライセンス**: CREPE=MIT（`crepe` extra）/ basic-pitch=Apache-2.0 / Demucs=
  `separate` extra。**Essentia(Melodia)=AGPL-3.0** はコピーレフトで、
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
