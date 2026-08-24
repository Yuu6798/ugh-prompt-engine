# DESIGN RUN9 — Revision 0.2

- **裁定日:** 2026-08-24
- **裁定者:** User
- **design_revision:** 0.1 → 0.2
- **対象正本:** [`DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md`](./DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md)（本ディレクトリ同梱・byte-pin 不変。design_doc_sha256 が実バイトの sha256 を PINNED で保持し続ける）

v0.1 本文は一切改変しない。本メモは v0.1 に対する**差分**のみを規定する
正規の改訂（DESIGN_RUN9 §31「User: 次世代交配・繁殖率・淘汰」およびヘッダ
注記「修正が必要なら Experiment ID は RUN9 のまま、design_revision を上げ、
旧 attempt を append-only 履歴として残す」に基づく手続き）。旧 revision
（"0.1"）を宣言する contract は Phase 0.2 以降 fail-closed で拒否される
（`run9_schema.DESIGN_REVISION` の凍結値照合）— これは意図どおりの拒否で
あり、実装バグではない。

---

## 改訂 1 — 学習アーキテクチャ: Performance Adapter → Founder 別 versioned Performance ControlProfile

v0.1 §13.1「RUN9 の primary learning mode は、Founder ごとの `Performance
Adapter` を更新する `LEARN_PERFORMANCE` とする」を、次のとおり改める:

> **`LEARN_PERFORMANCE` の書き込み先を、ニューラル `Performance Adapter`
> から、Founder ごとの versioned **Performance ControlProfile**
> （明示的・非ニューラルな制御パラメータの版付き集合）へ変更する。**Adapter
> への自動昇格は禁止**する。**凍結対象は不変**: Backbone / Genome /
> Identity coordinate / speaker embedding / model weights のいずれも
> `LEARN_PERFORMANCE` の書き込み対象にしない（v0.1 §13.1 の凍結列挙を
> そのまま継承する）。**Performance ControlProfile 自体は trainable な唯一の
> 書き込み先**として `Performance Adapter` を置き換える。**制御層の天井
> （control-layer ceiling）が後に確認された場合は、別 RUN または別
> design revision として扱う**（自動で Adapter 学習へ scope upgrade しない）。

**注記（用語の非同一性）**: 本 ControlProfile は `docs/control_profile.md`
（`CompositionScore.control_profile` — Suno/MusicGen 等の生成器ごとの
`grip_class` 自己記述ブロック）とは**別スキーマ・別ドメイン**である。
偶然の同名であり、VoiceGenesis Evolution（本 RUN9）の Founder Performance
制御パラメータと、Composition Score の生成器条件付けチャネルとを混同しない。

**根拠（正規の改訂手続きであることの明記）**: v0.1 §13.2 は「不足時:
`BLOCKED_ADAPTER_ENTRY`」の後に「制御層学習へ自動でscope downgradeしない。
**制御層版へ変更する場合は、学習開始前のdesign revisionとして記録する**」
と定めている。本改訂 1 はこの規定に基づく正規の scope downgrade 記録
そのものであり、v0.1 の規律を破っていない — 逆に、v0.1 が「後で天井を
確認したら記録して変える」ことを想定していた分岐そのものが発動した形。

---

## 改訂 2 — AF0 anchor 規約

`af0` の `anchor_hashes` は、`inputs/af0_anchor_manifest.json`
（schema `run9-af0-anchor-manifest/1.0`）の**正規形 sha256**を pin する
（WAV バイト列そのものの単一ハッシュではなく、AF-P0 正典証拠の複合参照
manifest のハッシュ）。

WAV 実体（`voicebank/AF0/*.wav`）が必要になった場合（RUN9 Founder 出生時の
実際の音声合成に AF0 Body が要る局面）は、以下を要件とする:

- WAV を再生成し、`results/AF0/SHA256SUMS.txt` との**全件一致**を要求する。
- 1 件でも不一致なら **repin せず停止**する（`af0_anchor_manifest.json` の
  `sha256sums_sha256` を書き換えて通す、という迂回は禁止）。

AF-P0 の判定は不変のまま継承する: `p0_results.json` の `overall.verdict`
= `NOT_ESTABLISHED`（failed_gates: G10/G11/G13、reason_codes:
DURATION_NOT_ESTABLISHED / ENERGY_NOT_ESTABLISHED /
AFTERGLOW_NOT_ESTABLISHED）。**Duration / Energy / AG-alpha の忠実な輸送は
主張しない**（v0.1 §7.1 の制限をそのまま継承 — RUN9 は AF0 を identity
anchor として使うが、AF-T0 の再判定を行わない）。

---

## 改訂 3 — PJS provenance 規約

PJS は役割別に2つの sha256 を pin する（v0.1 §7.4「External Curriculum
Provider」の役割分離をそのまま反映）:

| pin 名 | 対象 | 値 | 出典 |
|---|---|---|---|
| `source_archive_sha256` | 配布 zip 全体（PJS corpus ver1.1、100曲） | `683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca` | `voice_genesis/foundry/s1_dataprep/README.md`（素材2） |
| `expanded_corpus_identity_sha256` | WAV バイト込みの展開後コーパス identity | `9905cec08fbaf43fa545400498a7908ef28567e8f60a5ba005fb2e00d526f996` | `voice_genesis/foundry/adapter/presets/pjs_neutral.json` `corpus_sha256` |

この2値は**互いに代替ではない**（zip 全体 vs 前処理後コーパスの違いであり、
どちらも正 — 旧 v0.1 段階でこの2値の不一致を「ブロッカー」として記録して
いたのは誤認だった。実際は同一対象を指す2つの矛盾する値ではなく、**別の
対象**を指す2つの正しい値だった）。

RUN9 が実際に消費する Performance Lesson（v0.1 §11「PJS Performance
Lesson」）は、上記いずれとも別の**Lesson manifest**を Lesson build 時に
生成し、そのファイルの sha256 を `lesson_sha` として pin する
（`lesson_sha` は依然 PENDING — Lesson build 自体が VG-L0 ハーネス実装
待ちのため）。

---

## 改訂 4 — User donor rights 規約

rights manifest（`inputs/rights_manifest.json`、schema
`run9-user-donor-rights/1.0`）は **Fable 起草 + User attest** 方式を採る:

- Fable（Claude）が `voice_genesis/foundry/recording_kit/user_donor_ledger.json`
  の実測値（card_id / source_sha256 / sha256 / duration_sec 等）を転記して
  manifest を起草する。
- **User の確認前は `rights_class` / `consent_status` ともに
  `PENDING_USER_ATTESTATION`**。`attestation.attested` は `false` のまま。
- **raw 音源の公開・モデルの一般配布は別承認**（`usage_grants` の
  `raw_audio_publication` / `model_general_distribution` は初期値
  `"not_granted"` — RUN9 の identity anchor としての内部使用の可否
  （`run9_identity_anchor`）とは独立して管理する）。
- User attest 完了後、`rights_manifest.json` の sha256 を
  `identity_domain_run9_v1.json` の `anchor_hashes.user` を pin する入力の
  一部へ束ねる（束ね方の詳細は User attest 実施時に確定する — 現時点では
  `anchor_hashes.user` は `<PIN_BEFORE_RUN>` のまま）。

---

## 改訂 5 — Shared Backbone

**RUN6 phase B 40K checkpoint** を Shared Backbone として採用する。
**RUN7 は不使用**とする — 理由は、run6→run7 の単一介入が
**教師交代**（d3synth 合成教師の引退 → あみたろ実録音教師、
`voice_genesis/foundry/DESIGN_S6_run7.md` / `results_s6/s6_record_2026-08-20.md`）
であり、この教師交代の残留効果が RUN9 自身の教師変数（PJS Performance
Lesson）と混同されるリスクを避けるため。RUN6 checkpoint はこの教師交代
より前の状態であり、RUN9 の「同一 Backbone 上で PJS だけを教師として
導入する」という単一介入設計（v0.1 §2「本RunはEとLを同一タイミングで
変更しない」と同型の混入回避規律）に適合する。

| pin | 値 | 出典 |
|---|---|---|
| `checkpoint_sha256` | `6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a` | `results_s5/s5_record_2026-08-20.md` §5.2（`phase_b/checkpoints/model_ckpt_steps_40000.ckpt`、判定の正本）/ `results_s7/s7_exporter_input_pins.json` `generations.run6.checkpoint_sha256` / `results_s7/s7_0b_probe_spec.json` / `run8/provision.sh` `run6_ckpt` の4件で相互一致確認済み |
| `runtime acoustic ONNX sha256` | `aaaff716db116cf3b78b981d4bf5fa6e6ab414988995b25ba43ddc47f0f38706` | `results_s5/s5_record_2026-08-20.md` §5.4（run6 checkpoint からの export 成果物 `acoustic.onnx`） |

checkpoint 単体に加え、**runtime bundle manifest**
（`inputs/backbone_runtime_bundle.json`、schema
`run9-backbone-runtime-bundle/1.0`）を pin する — config.yaml / speaker map
/ phoneme dictionary / vocoder / render に使う DiffSinger リポジトリの
git revision まで含めた、実際に render を再現するために必要な入力一式の
束。詳細と出典一覧は同ファイルを参照。
