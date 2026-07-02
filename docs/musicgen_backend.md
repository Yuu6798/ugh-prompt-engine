# MusicGen ローカル生成トラック — 設計 doc

Status: PR A / PR C 実装完了、PR B 未着手
Scope: `facebook/musicgen-*`（transformers 経路）を第二生成器として組み込む計画

## 1. 目的と位置づけ

- **人手律速の解体**: Suno は人手 UI 生成が律速。MusicGen はローカル API 経由で
  バッチ生成でき、K/R 系トラックのサンプル数を機械的に増やせる。
- **第二機種の一般性実証**: `docs/control_profile.md` / `compose/device_profile.py`
  の device profile 機構は Suno 1 機種だけでは「Suno の癖」と「一般則」を区別
  できない。MusicGen は複数機種初実証の第一歩。
- **R3（確率的往復）の自動化**: `docs/roadmap_goal2.md` の R3 は生成の非決定性を
  n>1 で束ねる設計。人手生成では反復数が絞られるが、ローカル API なら
  バッチで反復できる。

MusicGen は**演奏者（生成側）**であり、`docs/learned_models_policy.md` の
annotation 隔離原則（学習モデルの出力を RPE evidence に混入させない）は対象外
——生成された音声は他の生成器と同様に librosa ベースの既存 RPE 抽出を通るだけで、
RPE の evidence 層に MusicGen 由来のラベルが直接書き込まれることはない。
ただし OSS ライセンス管理は本 doc の §5 で `docs/learned_models_policy.md` G4
（ライセンス gate）に準拠した verbatim 記録を行う。

## 2. 決定論契約（DD-A 踏襲）

`docs/controllability_poc.md` §4 の DD-A を継承する:

- 生成（MusicGen 推論）はリポジトリ外・非決定論のベストエフォート seed pin
  （`torch.manual_seed`）に留める。GPU/CPU・transformers バージョン間の完全
  一致は保証しない。
- 音声ファイル（WAV）は**コミットしない**。ローカルの生成物のみ。
- **fixture（数値）→ grip の区間のみが決定論・CI 対象**。fixture JSON
  （per-sample 数値特徴量）はコミットしてよく、`scripts/measure_grip.py` が
  それを読んで効果量を計算する処理は決定論的・単体テスト対象。
- CI は `musicgen` extra を一切インストールせず、torch なしで全テスト pass
  することが契約（`tests/test_musicgen_runbook.py` の実抽出テストのみ
  `@pytest.mark.slow`、plan 検証・seed 導出は torch 非依存の高速テスト）。

## 3. 既存配線の現状

- `musicgen` backend descriptor は `src/svp_rpe/compose/prompt_renderer.py`
  の `_BACKEND_DESCRIPTORS` に既存（`profile_key="musicgen"`,
  `negative_channel="negative_prompt"`）。`GeneratedPrompt.backend` の
  `Literal` にも既存。本 PR ではこの配線を一切変更しない。
- `examples/control/k0/musicgen_rpe_fixture.json`（`generator:
  "musicgen_fixture"`）は**手作りの最小 fixture であり実測ではない**。
  K0 は方法実証目的（`docs/controllability_poc.md` §4）で、K2 相当の実測
  fixture は本トラックの PR B が担う。

## 4. PR 分割

- **PR A（実装済み）**: runbook（`scripts/collect_musicgen_takes.py`）+
  `musicgen` extra + 本 doc。実推論は行わない・CI 安全な「器」のみ。
- **PR B（未着手）**: `scripts/collect_musicgen_takes.py generate` の実バッチ実行 →
  K2 型 fixture（`examples/control/k2_musicgen/fixture.json` 等）+
  `expected_grip.json` + `config/device_profiles/musicgen.yaml`
  （`docs/control_profile.md` の device profile 形式）。**§5 の G4
  ライセンス目視確認完了が前提条件**。
- **PR C（実装済み・実測待ち）**: R3-1/2/3 ハーネス（§6 参照）。

## 5. License

- **Code**: transformers（Apache-2.0）経由の MusicGen パイプライン
  （`MusicgenForConditionalGeneration`）を採用する。`audiocraft`
  （Meta 公式リファレンス実装、MIT だが依存が重い）には依存しない。
- **Weights**: `facebook/musicgen-small` は **VERIFY PENDING**——PR B 着手前に
  Hugging Face モデルカード（`https://huggingface.co/facebook/musicgen-small`）
  のライセンスバッジを目視確認し、`docs/learned_models_policy.md` §3.1 の
  laion-clap エントリと同様の verbatim 記録（URL 付き）をここに追記すること。
  期待値は **CC-BY-NC-4.0**（非商用条項）。もし目視確認でこの期待どおりなら、
  MusicGen の重みは**研究計器限定**の扱いとなり、プロダクト同梱（学習済み
  重みのリポジトリ同梱や商用配布物への組み込み）は不可。ローカル runbook で
  HuggingFace から実行時取得するだけの現行の使い方（重みを一切同梱しない）
  はこの制約下でも問題ない。
- G4 確認が完了するまで、PR B（実バッチ生成 → fixture コミット）には着手しない。

## 6. R3 接続 — 実装（PR C）

R3（確率的往復、`docs/roadmap_goal2.md`）の計器は実装済み:

- `src/svp_rpe/roundtrip/repetition.py` が本体。`corpus_batch.py` の
  `_regenerate_measured` パターン（音声 → `extract_rpe_from_file` →
  `draft_score` → 物理フィールド辞書）と同型の経路で各テイクを
  `diagnose_roundtrip`（`roundtrip/diagnose.py`）に通し、
  `TakeFieldObservation` へ蒸留する。`grip_map` は既定 `None`
  （＝ `diagnose_roundtrip` に空マッピングを渡し K1 grip map の既定ロードを
  回避する）。K1 は決定論シンセ演奏者の実測であり、確率的生成器の
  disagreement を誤って `knob_dead` に分類してしまうため。将来 MusicGen 専用
  grip fixture ができたら明示的に渡せる。
- `FieldRepetitionSummary`（`n` / `preserved_count` / `preserved_rate` /
  `diagnosis_counts` / `observed_values`）が n>1 のフィールド別往復一致率
  （R3-2）。`roundtrip/compare.py` の 4 値診断語彙（preserved /
  knob_dead / sensor_blind / calibration_disagreement）をそのまま使う。
- `SelectionResult`（`basis="preserved_field_count"`、`ranking`、
  `selected_take_id`）が rejection sampling（R3-3）。同数 `preserved_count`
  は `take_id` 昇順で決定論的にタイブレークする。**`selected_take_id` は
  「楽譜に最も近いテイクの機械的特定」であり品質判定ではない**（verdict
  語彙は一切出さない）。
- `load_takes_for_repetition` が PR A の takes manifest（`samples[]` の
  `audio_path`/`audio_sha256`）を読み、バイト再計算 sha256 と manifest pin
  の照合を fail-fast する（`collect_clap_fixture.py` 規約）。`excluded`
  サンプルはスキップする。
- テイク生成側は `scripts/collect_musicgen_takes.py perform` サブコマンド
  （PR A ファイルの拡張）: `ExternalPromptAdapter` で score をレンダリング
  し（`score.rendering.target_backend` に従う・backend override はしない）、
  `seed = seed_base + i` で N テイクを生成する。
- CLI: `svprpe roundtrip-rep <composition_score.yaml> <takes_manifest.json>`
  （`docs/cli.md` 参照）。
- **未実施**: 実バッチ生成・実測（MusicGen ローカル or Suno 手動）は本 PR の
  範囲外。計器の検証はテストの決定論シンセ演奏者による合成テイクで行った
  （honesty 維持・MusicGen 不要）。

## 7. 関連ドキュメント

- [`controllability_poc.md`](controllability_poc.md) — DD-A、K0-K3 の grip
  計測パターン全般
- [`control_profile.md`](control_profile.md) — `control_profile` / device
  profile スキーマ、PR1.5 のコンパイル配線
- [`learned_models_policy.md`](learned_models_policy.md) — annotation
  隔離原則、ライセンス gate（G4）
- [`roadmap_goal2.md`](roadmap_goal2.md) — R0-R5、R3 の位置づけ
