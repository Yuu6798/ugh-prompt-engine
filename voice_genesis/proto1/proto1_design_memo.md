# 試作品 1 号 設計メモ — 歌唱工房 PoC 骨格（VG-001/002/009/010/016 + linkability lite）

ゴール（設計書 §12 最初のマイルストーンの実装）:
「Voice Genome を触ると、意図した音響特徴が測定上動き、かつその声が誰にも
照合されないことを、版管理された手続きで確認できる」。
配置: scratchpad `proto1/` 配下（リポジトリは読み取り専用。vt_harness/ の
`voice_r0_1.py` / `measure_v3.py` は import 流用可・変更禁止）。
本メモが唯一の仕様正本。補充判断は `proto1/underspec_log_p1.md` に記録。

## P1. VG-001 — VoiceGenome v0.2 スキーマ

- `proto1/genome.py`: frozen dataclass 群 + JSON round-trip。
  セクション: source（tilt, source_mode）/ resonance（formant_scale,
  formant_offsets[4], bandwidth_scale）/ noise（breathiness_base,
  register_gains[5]）/ register（boundaries_midi[4], transition_width）/
  microprosody（vibrato_rate_hz, vibrato_depth_cents, jitter_amount,
  jitter_seed）/ range（lowest_midi, highest_midi）/
  physio_range（out_of_physio_range: bool, violated_bounds: list[str]）/
  audit（reference_set_hash: str|None, linkability_report_id: str|None,
  residual_gate_passed: bool|None = None ※R3 未実装につき None=not_applicable）
- **物理事前分布（§3.2 の具体化、凍結表）**:
  formant_scale [0.80, 1.25] / breathiness_base [0.0, 0.6] /
  tilt [-18.0, -3.0] dB/oct / vibrato_rate [4.0, 7.5] Hz /
  vibrato_depth [0, 150] cents / jitter_amount [0, 0.02] /
  register boundaries 昇順かつ [40, 96] / transition_width [1, 6] 半音。
  範囲外は生成拒否ではなく `out_of_physio_range=True` + `violated_bounds`
  に項目名を列挙（§1.5 楽器原理: 意図的範囲外設計を禁止しない）。
- 検証テスト: 正常系 round-trip、境界値、範囲外フラグ付与、不正型の拒否。

## P2. VG-002 — Probe Score Suite

- `proto1/probes.py`: 決定論 fixture 生成。probe 定義（凍結）:
  - `sustain`: {C3, A4, C6} × 1.5s 持続
  - `register_sweep`: MIDI 45→90 を半音階で 0.25s ずつ（声区遷移の連続性検査対象）
  - `vibrato`: A4 × 3s
  - `phrase`: 8 音の固定旋律（C4 D4 E4 G4 E4 D4 C4 C4、各 0.5s）— 「歌唱」の最小実演
  - `cross_range`: C3 と C6 の同一母音ペア（§7.1 Cross-range identity probe）
- manifest（JSON）に probe ごとの MIDI 列・長さ・sha256（波形 hash）を記録。
  同一 Genome → 同一 hash の決定論テスト付き。

## P3. VG-009 — Genome sampler / mutation

- `proto1/sampler.py`: `sample(seed)` = 物理事前分布内の一様サンプル（決定論）。
  `mutate(genome, seed, scale)` = ガウス摂動 + 事前分布境界でのフラグ更新。
  `crossover(a, b, seed)` = フィールド単位ランダム継承。
- テスト: 同一 seed → 同一 Genome、mutation の境界フラグ、再現性。

## P4. VG-010 — Registry + lineage

- `proto1/registry.py`: JSONL append-only ストア + sidecar 様式
  `genome-registry/0.1`。エントリ: genome_id（内容 sha256 の先頭 12 桁）/
  version / created_at(UTC ISO8601) / parents: [] / op(sample|mutate|crossover) /
  seed / renderer_version("R0.1") / feature_set_version / eval:
  {plausibility, grip_ref, novelty} / audit（reference_set_hash,
  linkability_report_id, residual_gate_passed=None）/ genome 本体。
- 系譜 API: `lineage(genome_id)` で親鎖を遡上。
- content hash は波形 float32 と JSON 正規形の sha256（svp-rpe
  `utils/hashing.py` と同型思想の自前実装。repo コードはコピーしない）。

## P5. VG-016 — reference-set/0.1 + VG-018 lite（linkability 監査）

- `proto1/reference_set.py`: sidecar `reference-set/0.1`
  {id, version, created_at, source_datasets, embedding_models,
  coverage_notes, sha256}。
- **スタンドイン gallery**: 「既知歌手」として 8 個の固定 seed Genome を
  レンダリング（sustain+phrase probe）し登録。coverage_notes に
  「合成スタンドイン。実在歌手 embedding は machine_dependent で未実装」と
  明記（正直会計）。
- **2 系統スタンドイン embedding**（§7.5 の 2 系統以上要件の手続き実装）:
  - E1: measure_v3 特徴ベクトル（probe 横断の頻度正規化済み集約）
  - E2: log-mel 帯域エネルギー平均ベクトル（librosa、64 帯域）
  ※ いずれも実在人間声で訓練された識別器ではない。レポートの provenance は
  必ず `measured (instrument-validity caveat: stand-in embeddings)` とする。
- 監査プロトコル（§7.5 の縮約実装）: 候補 Genome の probe レンダを probe、
  gallery 8 声を enrollment とし、コサイン類似で最近傍照合。
  チャンスレベル帯は **permutation（gallery ラベルシャッフル 200 回）** の
  最近傍類似分布から 95 パーセンタイルで推定。gate: E1・E2 の両系統で
  最近傍類似が帯以下 → PASS。報告書に reference_set_hash を必ずペア記録。
  reference set 更新（hash 変化）時に過去エントリへ `stale_audit` フラグを
  立てる再監査トリガーを実装。

## P6. レンダラ健全性テスト（VG-004/005/006 の Done 条件）

- aliasing: 加算合成の倍音が Nyquist 未満で打ち切られていることの検査
  （>0.45×sr 帯域のエネルギー比 < -40dB）。
- register transition: register_sweep probe でフレーム RMS の隣接差が
  6dB を超えないこと（遷移の連続性）。
- formant sweep: formant_scale 0.85→1.15 で formant_centroid_v3 が単調に
  動くこと（既存 grip 結果と整合。簡易確認で可）。

## P7. テストと実行様式

- `proto1/tests/test_*.py`（pytest、リポジトリの conftest に依存しない
  自己完結。`python -m pytest proto1/tests -q` で回る）。
- 全てフォアグラウンド・決定論（`Date.now` 系不使用、seed 固定）。
- 成果物: 上記コード + `proto1/underspec_log_p1.md` +
  `proto1/results_p1/scaffold_test_report.md`（テスト結果全文と件数）。

## P8. 本メモのスコープ外（後続の統合サイクルで実施）

- E2E デモ（sample→render→measure→audit→register の一括実行）と
  受け入れチェックリスト判定 — grip v4 の完成を待って統合する。
- VG-011（歌詞付き歌唱）/ VG-020（日本語音素）— 試作品 1 号の範囲外
  （§12 マイルストーン定義に含まれない）。phrase probe が最小の旋律実演。
