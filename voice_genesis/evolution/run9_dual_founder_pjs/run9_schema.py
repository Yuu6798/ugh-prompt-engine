"""run9_schema.py — RUN9 run-local 正本モジュール（Phase 0 スキャフォールド）。

`DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md`（本ディレクトリ同梱、
以下 DESIGN_RUN9）の §8/§9/§23 を実装する。VG-E0 の凍結三角形
（`voice_genesis/evolution/models.py` の `ANCHOR_NAMES = ("ritsu", "pjs", "user")`）
は DESIGN_RUN9 §8 の指示により一切変更しない。RUN9 は新しい run-local domain
`run9-af0-ritsu-user/1.0`（anchor_order: af0, ritsu, user）を本モジュールが
独立に定義する。VG-E0 の `simplex.py`/`models.py` はモジュールレベルで import
しない（domain が異なるため意味論だけを踏襲した独立実装 — DESIGN_RUN9 §8
「既存 schema・既存台帳を in-place 変更しない」）。

sibling import 流儀（`voice_genesis/evolution/` 全体の家風）を踏襲するため
`_THIS_DIR` を `sys.path` へ挿入する。ただし本モジュール自体は他の run9
sibling モジュールを import しない（現時点では単一ファイル）。

fail-closed 方針（models.py と同型）: 未知キー拒否、欠落キーのデフォルト
補完なし、公開 API に coords/weights の事後注入経路を作らない
（DESIGN_RUN9 §27 item 22 / §9.4「試聴後に0.55/0.45等へ調整してはならない」）。
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import re
import sys
import types
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

import yaml  # PyYAML は本体必須依存（pyproject.toml [project].dependencies）

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# ---------------------------------------------------------------------------
# 共通定数
# ---------------------------------------------------------------------------

SCHEMA_IDENTITY_DOMAIN = "voicegenesis-identity-domain/1.0"
SCHEMA_RUN_CONTRACT = "voicegenesis-run-contract/1.0"

RUN9_DOMAIN_ID = "run9-af0-ritsu-user/1.0"
RUN9_ANCHOR_ORDER: Tuple[str, str, str] = ("af0", "ritsu", "user")
RUN9_EXCLUDED_TEACHER_IDENTITIES: Tuple[str, ...] = ("pjs",)
RUN9_COORDINATE_PRECISION = 6
RUN9_NORMALIZATION = "largest-component-residual"

RUN_ID = "RUN9"
EXPERIMENT_ID = "VG-R9-DUAL-FOUNDER-PJS"

# 現行 design_revision（凍結値。User 裁定 2026-08-25 =
# DESIGN_RUN9_REVISION_0.4.md — 外部指摘（AQUEST 山崎信英氏）を受けた派生設計変更メモ
# `DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt` の採用）。旧 revision
# "0.1"/"0.2"/"0.3" を宣言する contract は意図どおり拒否される — 修正が
# 必要なら design_revision を上げ、旧 attempt を append-only 履歴として
# 残す規約（DESIGN_RUN9 ヘッダ注記）。
DESIGN_REVISION = "0.4"

# rev 0.3（改訂A、PoR §1/§3/§4/§16）: 単一 LEARN_PERFORMANCE エッジを
# CONTROL 無介入枝 + 二つの介入エッジ（PRACTICE_FROM_AUDIO / 稽古,
# TRANSFER_TECHNIQUE / 教育）へ分離する。r0 は交配（INHERIT_TRAIT）で
# 出生した後、両介入エッジとも独立 Revision（r_practice / r_taught）へ
# 分岐する — r0 自体は in-place 更新されない（PoR §10 最優先の不変条件）。
# 旧 CHANGED_EDGE 単一定数（= "LEARN_PERFORMANCE"）は rev 0.3 で廃止する
# （run_id/design_revision と同様に contract loader が fail-closed で
# 旧形式 `single_intervention.changed_edge` を拒否する — 下記
# `interventions` 構造を参照）。
INTERVENTION_EDGES: Tuple[str, str] = ("PRACTICE_FROM_AUDIO", "TRANSFER_TECHNIQUE")

# PoR §4 の3分岐のうち、学習介入を伴わない無介入 replay 枝（対照条件）。
CONTROL_BRANCH = "CONTROL"

# Codex bot レビュー PR #318 第7巡 Fix 20 採用（P1）: C0/C1 校正標本の
# per-founder テイク数を契約 pin する `interventions` 配下の pin 欄名
# （§ RUN9_CONTRACT.yaml `interventions.c0_replay_takes_per_founder` /
# `interventions.c1_sham_takes_per_founder`）。旧 identity_metric_space.json
# は「テイク数は本ファイルの `interventions` 規定に従う」と書きながら
# 実体が存在しない欠陥（存在しない参照）を持っていた — 本タプルが宣言する
# 2欄がその実体であり、`load_run9_contract()` が pin 欄と同型の
# {value, status, reason?, source?} 形（`_validate_pin_field()`）+ PINNED
# 時の正の int 型検証（`_require_positive_int()` — bool/float/0/負値を
# 拒否）で検証する。
INTERVENTION_TAKE_COUNT_FIELDS: Tuple[str, str] = (
    "c0_replay_takes_per_founder",
    "c1_sham_takes_per_founder",
)

# PoR §3.1「交配 — INHERIT_TRAIT」: 出生エッジの正式名。operator 自体は
# 引き続き `TRI_CROSSOVER/1.0`（genome_id 決定論を壊さない — エッジ名の
# 導入は TRI_CROSSOVER の計算規約を変更しない、あくまで結果分類・設計文書
# 上のラベル）。
BIRTH_EDGE = "INHERIT_TRAIT"

# DESIGN_RUN9 §6 の parent_designs 正典（凍結値。順序も含めて完全一致を
# 要求する — Codex bot レビュー PR #315 第7巡指摘2採用）。§6 は5件を宣言
# するが §23 の Run Contract 雛形は3件しか列挙していない設計書内部の
# erratum（第6巡指摘1で判明・contract 側で是正済み）があるため、完全側の
# §6 を正典として run-local に固定する（設計書自体は byte-pin 済みのため
# 一切編集しない）。
PARENT_DESIGNS: Tuple[str, ...] = (
    "voice_genesis/evolution/DESIGN_VG_E0.md",
    "voice_genesis/evolution/DESIGN_VG_L0.md",
    "VoiceGenesis Evolution Theory v0.3",
    "VoiceGenesis Singing Baseline v0.1",
    "VoiceGenesis Supplement A / Selection Pressure Routing",
)

# DESIGN_RUN9 §9.2/§9.3: 事前固定重み。genome 発行時の唯一の重みソース
# （公開 API から任意 weights を注入する経路は作らない — §27 item 22）。
R9F01_WEIGHTS: Tuple[float, float, float] = (0.6, 0.3, 0.1)
R9F02_WEIGHTS: Tuple[float, float, float] = (0.1, 0.3, 0.6)
SHARED_PERFORMANCE_SEED = 909001
LEARNING_SEED = 909002
MAX_HUMAN_AUDIT_PAIRS = 12

OPERATOR_ID = "TRI_CROSSOVER/1.0"

_FOUNDER_ID_RE = re.compile(r"^R9F-0[12]$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
# git commit object ID は SHA-1（40桁小文字hex）— repository_commit_sha は
# 他の *_sha 欄（sha256）と同じ64hex規則を課すと、正直な git sha を PINNED
# にしても構造的に READY へ到達できなくなる不備だった（第1巡修正時の
# 見落とし。Codex bot レビュー PR #315 第3巡指摘1採用）。
_SHA1_HEX_RE = re.compile(r"^[0-9a-f]{40}$")
# attempt_id の正の文法（Codex bot レビュー PR #315 第4巡指摘採用）: 先頭は
# 英数字、以降は英数字/`.`/`_`/`-` のみ。プレースホルダ変種
# （`" <PIN_BEFORE_RUN> "` のような前後空白、`<PIN_1>` のような数字入り等）を
# 個別にブラックリスト追撃するのではなく、`<`/`>`/空白を構造的に許容しない
# 正の文法で終端する。
_ATTEMPT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_GENOME_ID_LEN = 16
_GENOME_ID_RE = re.compile(rf"^[0-9a-f]{{{_GENOME_ID_LEN}}}$")

# ---------------------------------------------------------------------------
# rev 0.3（改訂D、PoR §13）: 結果分類語彙の凍結。単一 Total Score へ潰さず
# （§27 item 40 の禁則を rev 0.3 でも継承）、6分類をそれぞれ独立判定する。
# v0.1 §20 の transfer_status 語彙は本ファミリーにより superseded
# （DESIGN_RUN9_REVISION_0.3.md 改訂D参照）。値は PoR §13 の逐語。
# ---------------------------------------------------------------------------

BIRTH_OUTCOMES: Tuple[str, str] = ("ESTABLISHED", "NOT_ESTABLISHED")
PRACTICE_OUTCOMES: Tuple[str, str, str] = ("GAIN_ESTABLISHED", "NO_GAIN", "UNOBSERVABLE")
EDUCATION_OUTCOMES: Tuple[str, str, str] = ("TRANSFER_ESTABLISHED", "NO_TRANSFER", "UNOBSERVABLE")
SEPARATION_OUTCOMES: Tuple[str, str, str] = (
    "MACHINE_EVIDENCE_SUPPORTED", "MIXED", "NOT_ESTABLISHED",
)
FOUNDER_RESPONSE_OUTCOMES: Tuple[str, str, str] = (
    "DIFFERENTIAL_RESPONSE", "COMMON_RESPONSE", "UNDETERMINED",
)
IDENTITY_OUTCOMES: Tuple[str, str, str] = (
    "STABLE_BY_MACHINE_METRIC", "SHIFTED", "UNCALIBRATED",
)

# rev 0.3（User 外部レビュー PR #317 P2-4 採用、PoR §12）: held-out gain は
# 「実装可能なら」ではなく RUN9 の最低限の評価漏洩防止として必須。
# train-only gain と held-out gain を必ず別記録する4欄を凍結する
# （2枝 × 2区分 = 4）。「Generalization gain（実装可能な範囲）」という
# 旧い努力目標的な扱いと、この4欄の必須性を混同しない — 任意・後続精密化
# 欄は別途 `OPTIONAL_GENERALIZATION_FIELDS` として区別する。
REQUIRED_GAIN_FIELDS: Tuple[str, str, str, str] = (
    "practice_train_gain",
    "practice_heldout_gain",
    "education_train_gain",
    "education_heldout_gain",
)

# 任意・後続精密化欄（PoR §7 item 7「Generalization gain（実装可能な
# 範囲）」に相当 — RUN9 単体では一般化の精密証明を要求しないため、この
# 3欄は実装できる範囲でのみ記録すればよい）。
OPTIONAL_GENERALIZATION_FIELDS: Tuple[str, str, str] = (
    "broad_generalization_gain",
    "cross_song_generalization",
    "cross_register_generalization",
)

# rev 0.3（改訂E、PoR §9）: 失敗の三分類。IMPLEMENTATION_FAILURE は修正可・
# 同一 design_revision で再 attempt。SCIENTIFIC_NULL は結果として凍結
# （閾値・lesson・探索範囲を緩めて同 attempt を救済しない）。DESIGN_FAILURE
# は現 revision を凍結し新 design_revision で再構築する。
FAILURE_CLASSES: Tuple[str, str, str] = (
    "IMPLEMENTATION_FAILURE", "SCIENTIFIC_NULL", "DESIGN_FAILURE",
)

# ---------------------------------------------------------------------------
# rev 0.3 改訂D（User 外部レビュー PR #317 P1-4 採用）: 科学結果
# （scientific_outcomes = 上記6分類）と運用状態（run_status）と
# 保存/昇格（archive_status/promotion_status）を完全に分離する。
#
# 未来の実装者が6分類を記録した後に旧 v0.1 §20 の overall PASS /
# PASS_WITH_RESIDUAL へ再集約し、そのPASSから §21 CANONICAL_LEARNED_REVISION
# / Parent Pool 昇格を導出できてしまう抜け道を、語彙レベルで塞ぐ
# （3つの語彙は互いに素であり、いずれの語彙からも他方を機械的に導出する
# 関数は本モジュールに存在しない — 「6分類→単一PASS/TotalScore」を
# 生成する関数は意図的に実装しない）。
# ---------------------------------------------------------------------------

# run_status: 実行完了状態だけを示し、科学的優劣・PASS/FAIL を表さない
# （PoR §9 の三失敗分類 = FAILURE_CLASSES とは別軸 — run_status は
# 「実行がどう終わったか」、FAILURE_CLASSES は「終わり方が失敗だった場合
# その失敗をどう分類するか」）。
RUN_STATUSES: Tuple[str, str, str, str] = (
    "COMPLETE", "BLOCKED", "IMPLEMENTATION_FAILED", "DESIGN_FAILED",
)

# archive_status: 全 terminal attempt（gain 成立・NO_GAIN/NO_TRANSFER・
# SCIENTIFIC_NULL・DESIGN_FAILURE/UNOBSERVABLE・Identity SHIFTED・
# incomplete/failed attempt の証拠を含む）が対象。値は単一
# （IMMUTABLE_ARCHIVED）のみ — 「保存するかどうか」の分岐自体を存在させない
# （全 terminal outcome で無条件に作成する規律を語彙レベルで強制する）。
ARCHIVE_STATUSES: Tuple[str] = ("IMMUTABLE_ARCHIVED",)

# promotion_status: RUN9 単体の結果からは絶対に昇格値へ到達できない
# ことを語彙レベルで保証する — 値はただ1つ（ARCHIVE_ONLY_PENDING_USER_RULING）
# のみで、"CANONICAL_LEARNED_REVISION" のような昇格を意味する値は
# PROMOTION_STATUSES に一切含まれない。Parent Pool 登録・
# CANONICAL_LEARNED_REVISION への自動昇格・片方だけの優良 Founder 選抜・
# PASS からの自動繁殖適格判定は rev 0.3 で禁止する（PoR §14「繁殖、淘汰、
# Parent Pool 昇格、優良 Founder 選抜は後続裁定へ送る」）。昇格が必要に
# なった場合は、本 frozenset を拡張する新しい design_revision（= 別の
# User 裁定）を要する。
PROMOTION_STATUSES: Tuple[str] = ("ARCHIVE_ONLY_PENDING_USER_RULING",)

# rev 0.3 改訂A/D（User 外部レビュー PR #317 P1-3 採用）: CONTROL 枝は
# 内部に二つの必須 control condition を持つ — C0（無学習 replay。
# renderer/backend/PCM の自然変動＝noise floor を測る）と C1（中立
# ControlProfile を付与するだけで学習 step は実行しない Sham Transition。
# ControlProfile 機構を通すこと自体の副作用を測る）。旧実装はこの二つを
# 「CONTROL = 無介入 replay」の単一概念へ混同しており、render/replay
# noise と profile 適用機構の副作用を分離できなかった（rev 0.2 の C1
# Zero ControlProfile / Sham Transition の意味論と rev 0.3 当初の CONTROL
# 定義が矛盾していた）。
CONTROL_CONDITIONS: Tuple[str, str] = ("NO_LEARNING_REPLAY", "ZERO_CONTROLPROFILE_SHAM")

# rev 0.3（改訂A、PoR §4/§10）: 各枝・各 control condition が書き込む
# Founder 別 versioned Performance ControlProfile（改訂1で導入済みの
# ControlProfile 方式を三枝へ拡張）のバージョン系列命名。CONTROL 枝は
# condition 別に2値（C0=NO_LEARNING_REPLAY→"replay" / C1=
# ZERO_CONTROLPROFILE_SHAM→"r_sham"）を持つネスト mapping、
# PRACTICE_FROM_AUDIO/TRANSFER_TECHNIQUE は各々独立の単一 revision 系列
# （r_practice / r_taught）として保存する — r0 を in-place 更新しないこと
# と対で、両枝の変化を後から比較可能にする。
BRANCH_REVISIONS: Mapping[str, Any] = types.MappingProxyType({
    CONTROL_BRANCH: types.MappingProxyType({
        "NO_LEARNING_REPLAY": "replay",
        "ZERO_CONTROLPROFILE_SHAM": "r_sham",
    }),
    "PRACTICE_FROM_AUDIO": "r_practice",
    "TRANSFER_TECHNIQUE": "r_taught",
})


def control_conditions_satisfied(observed_conditions: Any) -> bool:
    """評価 readiness 判定（rev 0.3 改訂A/D、User 外部レビュー PR #317
    P1-3 必須テスト「C0/C1 の片方が欠けた attempt は評価 READY にならない」
    の機械実装）。`observed_conditions` は attempt が実際に生成した
    control condition の名前集合（例: `{"NO_LEARNING_REPLAY"}`）。
    `CONTROL_CONDITIONS` の全件が揃って初めて True — Practice/Education
    gain の基準ノイズ算出（C0 由来）と profile 機構の副作用記録
    （C1−C0）はどちらも両条件の存在を前提とするため、片方だけでは
    評価 READY と判定しない。"""
    if not isinstance(observed_conditions, (set, frozenset, list, tuple)):
        raise Run9ValidationError(
            f"observed_conditions must be a set/list/tuple, got {type(observed_conditions).__name__}"
        )
    return set(CONTROL_CONDITIONS).issubset(set(observed_conditions))

# ---------------------------------------------------------------------------
# rev 0.3（改訂C、PoR §3.2/§3.3/§11）: PRACTICE / EDUCATION の情報境界の
# 凍結定数。機械可読 id で列挙し、将来の practice/education builder が
# これを import して「渡してよいもの」「渡してはいけないもの」の実装時
# 検証の正本として使う（本モジュール自体はまだ builder を持たない —
# ハーネス実装は VG-L0 待ち。ここでは語彙の凍結のみ行う）。
# ---------------------------------------------------------------------------

# PoR §3.2「稽古で Founder へ明示的に渡してはいけないもの」。
PRACTICE_FORBIDDEN_INPUTS: Tuple[str, ...] = (
    "pjs_speaker_embedding",
    "pjs_identity_coordinate",
    "correct_technique_parameter",  # 例:「vibrato=この値」等の正解 Technique parameter
    # PoR §3.2 冒頭「教師の正解パラメータやTechnique labelは与えず」の
    # 後半（Technique label チャネル）— PR #317 Codex bot レビュー第2巡
    # Fix 4 採用: 第1巡実装時に転記漏れしていた（正解 parameter の禁止は
    # 上の correct_technique_parameter でカバー済みだったが、教師付与の
    # ラベルそのもの — 「これは vibrato の見本」といった名前付けの供与 —
    # は別の禁止項目として明示されていなかった）。
    "teacher_technique_label",
    "teacher_internal_parameter_dump",
)

# rev 0.3（User 外部レビュー PR #317 P2-1 採用）: 旧 `PRACTICE_ALLOWED_INPUTS`
# は「データ入力」（pjs_audio_direct_listen）と「Founder 自身が行う動作」
# （feature extraction / target selection / diff estimation / search）を
# 一つのタプルに混在させていた。将来の practice builder がこれを単純な
# 入力 allowlist として扱うと、「Founder 自身が差分抽出する」という actor
# 境界（誰が/何を渡され/何を自分でするか）を正確に強制できない
# （外部から動作結果だけを渡して「入力として許可されている」と偽装できて
# しまう）。3分割し、旧定数は削除する（本 PR 内でのみ使われていた語彙の
# ため後方互換エイリアスは不要）。

# 許可データ入力: Founder へ実際に渡してよい「情報」そのもの。
PRACTICE_ALLOWED_DATA_INPUTS: Tuple[str, str, str] = (
    "pjs_training_audio",
    "founder_self_render",
    # 全枝共通の score/lyrics/task context — 教師 Performance から導出した
    # 正解情報ではない場合のみ許可（歌詞・楽曲構造そのものは教師の歌唱表現
    # の答えではないため）。
    "shared_score_lyrics_task_context_non_teacher_derived",
)

# 必須の Founder-local 処理: 外部から代行させてはならない、Founder 自身が
# 行うべき動作（「同じ feature extractor コードを利用する」こと自体は
# 禁止しないが、抽出という行為自体は Founder 側で実行されなければ
# ならない — PoR §3.2「Founder 自身が...」の主語を機械可読にした語彙）。
PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS: Tuple[str, str, str, str, str] = (
    "feature_extraction",
    "imitation_target_selection",
    "self_teacher_difference_estimation",
    "candidate_generation",
    "allowed_range_search",
)

# 明示的に禁止する外部支援: 教師側で既に計算済みの「答え」を
# PRACTICE learner へ入力として渡す経路（Founder 自身の自律処理を代行・
# 迂回してしまう）。
PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE: Tuple[str, ...] = (
    "precomputed_teacher_technique_features",
    "externally_selected_imitation_target",
    "teacher_derived_diff_vector",
    "correct_target_trajectory",
    "teacher_loss_gradient",
    "education_lesson_reference",
    "speaker_identity_embedding",
    "teacher_internal_parameter_dump",
)

# PoR §3.3「教育で渡してよい候補」（= §11 TRANSFER_TECHNIQUE の許可
# channel）。
EDUCATION_ALLOWED_CHANNELS: Tuple[str, ...] = (
    "timing",
    "phoneme_note_duration_relation",
    "pitch_trajectory",
    "dynamics_energy_trajectory",
    "onset_release_pattern",
    "vibrato_pattern",
    "phrasing",
    "phrase_end_control",
    "breath_placement",
)

# PoR §3.3「教育で渡してはいけないもの」+ §11「原則として learner 自身は
# PJS raw audio を直接参照しない」（lesson 生成器だけが PJS audio から
# Technique を抽出する非対称性そのものが実験変数 — PoR §11 末尾）。
EDUCATION_FORBIDDEN_INPUTS: Tuple[str, ...] = (
    "pjs_speaker_embedding",
    "pjs_identity_coordinate",
    "pjs_voice_quality_latent",
    "formant_inheritance_target",
    "spectral_envelope_identity_replication",
    "founder_identity_replacement_parameter",
    "learner_pjs_raw_audio_direct_reference",
)

# ---------------------------------------------------------------------------
# rev 0.3 改訂A（User 外部レビュー PR #317 P1-1 採用）: 稽古と教育の
# 書き込み境界が同一だった不備の是正。ControlProfile の可変領域を型付きで
# 分割し、枝ごとの書込許可を機械可読に固定する。`inputs/branch_write_policy.json`
# （schema 下記 `SCHEMA_BRANCH_WRITE_POLICY`）が同じ内容を人間可読な
# manifest として保持し、本モジュールの定数と一致することを load 時に
# 強制する（改変 manifest は load 失敗 — `validate_branch_write_policy_manifest()`
# 参照）。
# ---------------------------------------------------------------------------

SCHEMA_BRANCH_WRITE_POLICY = "run9-branch-write-policy/1.0"

# ControlProfile の可変領域を最低限、型付きで分割した state partition。
# IDENTITY_STATE はどの枝からも書き込み不可（`IMMUTABLE_STATE_PARTITIONS`
# 参照）。TRAIT_CONTROL は「明示的に許可された発声制御領域の後天的
# 変化」（PoR §3.2 の Trait 学習 — speaker embedding や Genome の変更では
# ない）を表す制御パラメータ領域、TECHNIQUE_CONTROL は歌唱の型・技術を
# 表す制御パラメータ領域。
STATE_PARTITIONS: Tuple[str, str, str] = (
    "IDENTITY_STATE", "TRAIT_CONTROL", "TECHNIQUE_CONTROL",
)

# 全枝で書込不可の state partition（`BRANCH_WRITABLE_PARTITIONS` のどの
# 枝の writable 集合にも現れない）。
IMMUTABLE_STATE_PARTITIONS: Tuple[str] = ("IDENTITY_STATE",)

# 枝ごとの書込許可 partition 集合（凍結・機械可読）。CONTROL は学習 step を
# 実行しないため writable = 空。PRACTICE_FROM_AUDIO は稽古で Trait +
# Technique の両方が自律的に動き得る（PoR §3.2「Trait/Techniqueの両方が
# 変化し得ることを観測する」）。TRANSFER_TECHNIQUE は Technique のみ —
# 型だけの伝達であり Trait は書き換えない（教育枝まで Trait を書き換え
# られると「型だけの伝達」でなくなるという User レビュー指摘そのものの
# 是正）。
BRANCH_WRITABLE_PARTITIONS: Mapping[str, Tuple[str, ...]] = types.MappingProxyType({
    "CONTROL": (),
    "PRACTICE_FROM_AUDIO": ("TRAIT_CONTROL", "TECHNIQUE_CONTROL"),
    "TRANSFER_TECHNIQUE": ("TECHNIQUE_CONTROL",),
})

# 全枝で不変の artifact（state partition ではなく、そもそも
# ControlProfile の外側にある永続 artifact）。PRACTICE で許す Trait 変化は
# これらのいずれでもない — speaker embedding や Genome 変更そのものでは
# なく、TRAIT_CONTROL partition 内の「明示的に許可された発声制御領域の
# 後天的変化」に限定される（User 外部レビュー PR #317 P1-1 指摘6）。
BRANCH_IMMUTABLE_ARTIFACTS: Tuple[str, ...] = (
    "shared_backbone",
    "founder_genome",
    "identity_coordinate",
    "speaker_embedding",
    "model_weights",
    "r0_bytes",
)

# rev 0.3 改訂F 拡張（User 外部レビュー PR #317 P2-2 採用）: human_audit_mode
# の語彙。既定は DISABLED（今回の User 裁定に従う既定値）。
# ADVISORY_PREDECLARED は「監査を予定したが準備できていない」を
# DISABLED（監査を実施しない）と区別するための宣言 — holdout 開封後の
# モード変更は禁止し、SCIENTIFIC_NULL/Identity SHIFTED の救済に人間監査を
# 使わない規律と対になる（DESIGN_RUN9_REVISION_0.3.md 参照）。
HUMAN_AUDIT_MODES: Tuple[str, str] = ("DISABLED", "ADVISORY_PREDECLARED")
DEFAULT_HUMAN_AUDIT_MODE = "DISABLED"

# ---------------------------------------------------------------------------
# rev 0.4（DESIGN_RUN9_REVISION_0.4.md、外部指摘（AQUEST 山崎信英氏）を受けた派生設計変更メモ
# `DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt` の採用）: R9-G1 拡張・Performance
# Trait/Identity 除外語彙・LessonRecord 標準仕様・performance_source
# ブロックの凍結定数。実体 tooling（R9-G1 の pin 値実物照合・LessonRecord
# manifest の実 build）は machine-dependent 作業として引き続き VG-L0
# ハーネス実装待ち — 本節はいずれも語彙・構造・型のみを凍結する
# （既存 PRACTICE/EDUCATION manifest validator と同じ「骨組み凍結」
# パターン）。
# ---------------------------------------------------------------------------

# --- R9-G1 拡張（派生設計変更メモ変更5） -----------------------------------------
# v0.1 §19「R9-G1 INPUT_FREEZE_AND_RIGHTS」（byte-pin 不変）に対する rev 0.2
# 方式（Adapter→ControlProfile と同型の「読み替え」— v0.1 本文は書き換え
# ない）の意味名追加。gate ID（R9-G1）自体は変わらない。
R9_G1_ID = "R9-G1"
R9_G1_LEGACY_NAME = "INPUT_FREEZE_AND_RIGHTS"  # v0.1 §19 原文名（不変）
R9_G1_SEMANTIC_NAME = "RIGHTS_AND_PROVENANCE_GATE"  # rev 0.4 追加読み替え（派生設計変更メモの逐語）

# PASS 条件8項目（派生設計変更メモ「変更5」の逐語8項目を機械可読 id へ写した
# 凍結 tuple）。**gate は構造述語**（PR #316 第4巡の層分離規約を rev 0.4 でも
# 維持——`gate_state()` の docstring と `verify_rights_manifest_against_ledger()`
# 直前のコメント参照）: 各条件 id の実体照合（例えば「Voice Source が実際に
# 特定されているか」の中身の正しさ）は R9-G1 tooling（machine-dependent）の
# 職務のまま。本 tuple は8条件の名前と順序のみを凍結する。
R9_G1_PASS_CONDITIONS: Tuple[str, str, str, str, str, str, str, str] = (
    "VOICE_SOURCE_IDENTIFIED",
    "VOICE_USAGE_TERMS_CONFIRMED",
    "PERFORMANCE_AUTHOR_IDENTIFIED",
    "PERFORMANCE_USAGE_TERMS_CONFIRMED",
    "COMPOSITION_RIGHTS_CONFIRMED",
    "RECORDING_MASTER_RIGHTS_CONFIRMED",
    "TEACHER_SOURCE_VS_VOICE_IDENTITY_SOURCE_DISTINGUISHED",
    "NO_UNKNOWN_RIGHTS_HOLDER",
)

# FAIL 語彙（派生設計変更メモの逐語）。既存 `FAILURE_CLASSES`（rev 0.3
# 改訂E、3分類）とは別軸——`FAILURE_CLASSES` は attempt 全体の失敗の性質
# （実装ミス/科学的無効/設計不能）を分類し、こちらは R9-G1 という個別
# gate 1つの FAIL 値である。R9-G1 が不成立の attempt は、原因次第で
# `FAILURE_CLASSES` のいずれにも分類され得る独立した2層の語彙であり、
# 本定数は `FAILURE_CLASSES` を置換しない。
GATE_FAIL_RIGHTS_PROVENANCE_UNRESOLVED = "RIGHTS_PROVENANCE_UNRESOLVED"


def r9_g1_pass_conditions_declared(declared_conditions: Any) -> bool:
    """R9-G1 の8 PASS 条件（`R9_G1_PASS_CONDITIONS`）が `declared_conditions`
    （attempt が宣言した条件 id の集合）に全件含まれるかどうかの**構造
    判定のみ**を行う（`control_conditions_satisfied()` と同型のパターン）。
    各条件の実体照合（宣言が事実として正しいか）はこの関数の範囲外——
    R9-G1 tooling の職務のまま変更しない（gate=構造述語、実物照合=tooling
    という PR #316 第4巡の層分離規約を rev 0.4 でも維持する）。"""
    if not isinstance(declared_conditions, (set, frozenset, list, tuple)):
        raise Run9ValidationError(
            "declared_conditions must be a set/list/tuple, got "
            f"{type(declared_conditions).__name__}"
        )
    return set(R9_G1_PASS_CONDITIONS).issubset(set(declared_conditions))


# --- performance_source ブロック（派生設計変更メモ変更1 + 2026-08-25 User 追加
#     裁定「確認メモ / RUN9 用語整理」） -----------------------------------
# `RUN9_CONTRACT.yaml` 新設トップレベル欄 `performance_source` の凍結値。
# User 追加裁定の指示2「置換でなく追加」に従い、既存 teacher 表記
# （v0.1 §7.4/§11/§19 R9-G6/R9-G7 等、byte-pin 不変の運用上の呼称）は
# 書き換えず、本ブロックが Voice Source ≠ Performance Source ≠
# Performance Author の分離を明示する非所有注記の置き場所を担う。
PERFORMANCE_SOURCE_ID = "PJS"
PERFORMANCE_SOURCE_ROLE = "EXTERNAL_PERFORMANCE_SOURCE"

# 「teacher」語の非所有注記（User 追加裁定 2026-08-25、指示1の逐語）。
# `RUN9_CONTRACT.yaml` `performance_source.teacher_terminology_note` /
# `inputs/identity_metric_space.json` confuser_control 節の role 注記が
# この文言を参照する（一言一句同一である必要はないが、Voice 所有者を
# 意味しない旨と rights_manifest provenance を正とする旨の2点は必須）。
TEACHER_TERMINOLOGY_NOTE = (
    "Teacher は運用上の呼称であり Voice 所有者・Voice Identity Owner を意味"
    "しない（Voice Source ≠ Performance Source ≠ Performance Author の分離"
    "は rev 0.4 / inputs/rights_manifest.json の provenance を正とする）。"
)


# --- Performance Residual / Identity 除外語彙（派生設計変更メモ変更3・6） ----------

# 「歌い方」の定義修正（派生設計変更メモ変更3、逐語9項目）: RUN9 が Performance
# Source から抽出・移送してよい Performance Residual（= Performance Residual）
# の正準語彙。v0.1 §11「PJS Performance Lesson」の F0_lesson/Duration_lesson/
# Energy_lesson/End_lesson 等（byte-pin 不変）は、本語彙の RUN9 固有の初期
# 実装例として引き続き有効——本語彙が旧定義（「PJSの歌い方を移植する」）を
# 置き換える正典。
PERFORMANCE_RESIDUAL_VOCAB: Tuple[str, str, str, str, str, str, str, str, str] = (
    "relative_F0",
    "duration_ratio",
    "onset_offset",
    "energy_envelope",
    "vibrato",
    "phrase_dynamics",
    "attack_behavior",
    "release_behavior",
    "articulation_timing",
)

# Identity 除外 Trait の正準語彙（派生設計変更メモ変更3の6項目 + 変更6の4項目を
# 統合。重複概念（speaker/timbre/formant の3組）は統一名 + 別名注記で吸収し
# 7項目へ収束する — DESIGN_RUN9_REVISION_0.4.md「変更3・6」表参照）。
#
# 既存 `EDUCATION_FORBIDDEN_INPUTS`/`PRACTICE_FORBIDDEN_INPUTS`（rev 0.3）
# との関係: 別の層であり、どちらか片方を変更しても他方は自動的に変わらない
# （別の凍結対象）。`IDENTITY_EXCLUDED_TRAIT_VOCAB` は「Performance Residual
# として扱ってはならない特徴クラスの一般的な正準分類」（LessonRecord の
# `explicitly_excluded_identity_traits` が完全含有すべき対象）であり、
# `EDUCATION_FORBIDDEN_INPUTS`/`PRACTICE_FORBIDDEN_INPUTS` は「RUN9・PJS
# 固有の、特定の入力チャネルとして渡してはならない具体的禁止項目の列挙」
# である。前者は特徴の分類学、後者は運用上の入力境界。
IDENTITY_EXCLUDED_TRAIT_VOCAB: Tuple[str, str, str, str, str, str, str] = (
    "speaker_embedding",  # 変更3「speaker embedding」+ 変更6「speaker_embedding」
    "timbre_identity",  # 変更3「timbre identity」+ 変更6「timbre_embedding」（別名）
    "formant_identity",  # 変更3「formant identity」+ 変更6「formant_profile」（別名）
    "spectral_identity",  # 変更3のみ「spectral identity」
    "voice_genome",  # 変更3のみ「Voice Genome」
    "source_specific_identity_representation",  # 変更3のみ「source-specific identity representation」
    "identity_vector",  # 変更6のみ（RUN9 の Identity 座標/genome coordinate 一般を指す汎用項目）
)


# --- LessonRecord 標準仕様（派生設計変更メモ変更6） -----------------------------

SCHEMA_LESSON_RECORD = "run9-lesson-record/1.0"

# 派生設計変更メモの LessonRecord 雛形を機械可読キーへ写した最低要件。
LESSON_RECORD_REQUIRED_KEYS: Tuple[str, ...] = (
    "schema",
    "lesson_id",
    "performance_source",
    "voice_source",
    "performance_author",
    "composition_source",
    "recording_source",
    "extracted_traits",
    "explicitly_excluded_identity_traits",
    "rights_manifest",
    "provenance_manifest",
)

# 派生設計変更メモの変更6が使う5つの略記名（extracted_traits 節の逐語）を
# `PERFORMANCE_RESIDUAL_VOCAB` の正準名へ解決する対応表。`relative_F0` は
# 恒等（両語彙で綴りが同一）。
LESSON_RECORD_TRAIT_ALIASES: Mapping[str, str] = types.MappingProxyType({
    "relative_F0": "relative_F0",
    "duration": "duration_ratio",
    "timing": "onset_offset",
    "dynamics": "energy_envelope",
    "articulation": "articulation_timing",
})


def resolve_lesson_record_trait_alias(name: Any) -> str:
    """LessonRecord の `extracted_traits` 要素1件を正準 `PERFORMANCE_RESIDUAL_VOCAB`
    名へ解決する。`LESSON_RECORD_TRAIT_ALIASES` の略記名、または
    `PERFORMANCE_RESIDUAL_VOCAB` の正準名そのものを受理し、いずれでもない
    文字列は拒否する（fail-closed — 未知の trait 名を無言で通さない）。"""
    if not isinstance(name, str):
        raise Run9ValidationError(f"trait name must be a string, got {name!r}")
    if name in LESSON_RECORD_TRAIT_ALIASES:
        return LESSON_RECORD_TRAIT_ALIASES[name]
    if name in PERFORMANCE_RESIDUAL_VOCAB:
        return name
    raise Run9ValidationError(
        f"unknown Performance Residual name {name!r} — must be one of "
        f"{list(LESSON_RECORD_TRAIT_ALIASES.keys())} (aliases) or "
        f"{list(PERFORMANCE_RESIDUAL_VOCAB)} (canonical names)"
    )


def validate_lesson_record(data: Mapping[str, Any]) -> None:
    """LessonRecord（派生設計変更メモ変更6）の最低要件を検証する。
    `validate_practice_split_manifest()`/`validate_education_lesson_manifest()`
    と同じ「骨組み凍結」パターン——実体 build（実際の抽出結果）は
    machine-dependent 作業として VG-L0 ハーネス実装待ちのため、本関数は
    構造・語彙のみを検証する。fail-closed（未知キー拒否・欠落キーの
    デフォルト補完なし）。

    provenance 系フィールド（performance_source/voice_source/
    performance_author/composition_source/recording_source）は
    **外部事実欄**——PJS 側（外部第三者）の事実を記述する欄のため rev 0.4
    語彙予約（User 帰属専用 `<PENDING_USER_ATTESTATION>` / 外部第三者
    未解決 `<UNRESOLVED_EXTERNAL>`）を適用し、`<PENDING_USER_ATTESTATION>`
    を fail-closed で拒否し未解決は `<UNRESOLVED_EXTERNAL>` のみ許容する
    （`validate_rights_manifest_four_layer()` の provenance ブロック
    誤用拒否と同じ規約 — Codex bot レビュー PR #319 第7巡指摘 Fix 15、
    P2、採用）。

    一方 rights_manifest/provenance_manifest の2欄は**参照/pin欄**——
    実在する rights/provenance manifest への具体的な参照を保持する欄で
    あり、上記の外部事実欄とは性質が異なる。未解決 placeholder の
    居場所ではないため `<PENDING_USER_ATTESTATION>` /
    `<UNRESOLVED_EXTERNAL>` の**両 sentinel**を fail-closed で拒否し、
    実在する参照（非空文字列。形式は `performance_source.rights_manifest_ref`
    と同じ規約に倣い規定しない）を必須とする（Codex bot レビュー PR #319
    第9巡指摘 Fix 18、P2、採用 — 第7巡 Fix 15 は `<UNRESOLVED_EXTERNAL>`
    への代替を推奨したため、両欄がその値になった record が使用可能な
    参照/pin を一切持たないまま構造的に valid となる抜け道が残っていた）。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(f"lesson record must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - set(LESSON_RECORD_REQUIRED_KEYS)
    if unknown:
        raise Run9ValidationError(f"lesson record has unknown key(s): {sorted(unknown)}")
    missing = set(LESSON_RECORD_REQUIRED_KEYS) - set(data.keys())
    if missing:
        raise Run9ValidationError(f"lesson record missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_LESSON_RECORD:
        raise Run9ValidationError(
            f"lesson record schema must be exactly {SCHEMA_LESSON_RECORD!r}, got {schema!r}"
        )

    lesson_id = data["lesson_id"]
    if not isinstance(lesson_id, str) or not lesson_id.strip():
        raise Run9ValidationError(f"lesson record lesson_id must be a non-empty string, got {lesson_id!r}")

    for field in (
        "performance_source", "voice_source", "performance_author",
        "composition_source", "recording_source",
    ):
        value = data[field]
        if not isinstance(value, str) or not value.strip():
            raise Run9ValidationError(
                f"lesson record.{field} must be a non-empty string, got {value!r}"
            )
        # rev 0.4 語彙予約（User 帰属専用 `<PENDING_USER_ATTESTATION>` /
        # 外部第三者未解決 `<UNRESOLVED_EXTERNAL>`）を LessonRecord
        # provenance 系フィールドへ適用する——本節の5フィールドは全て
        # PJS 側（外部第三者）の事実を記述する欄であり、User 帰属欄専用の
        # `<PENDING_USER_ATTESTATION>` を fail-closed で拒否する
        # （Codex bot レビュー PR #319 第7巡指摘 Fix 15、P2、採用）。
        if value == _RIGHTS_MANIFEST_PENDING_USER_ATTESTATION:
            raise Run9ValidationError(
                f"lesson record.{field} is an external-fact field (PJS 側の事実欄); "
                f"{_RIGHTS_MANIFEST_PENDING_USER_ATTESTATION!r} is reserved for "
                f"User-attributable fields — use {_RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL!r} instead"
            )
        # Codex bot レビュー PR #319 第13巡指摘, Fix 26（P2, 採用）: 本節5欄も
        # `_validate_rights_provenance_block()` と同じ「外部事実の具体値を
        # 自由記述として受理する」経路であり、`USER_ATTESTED_OWN_VOICE`
        # （voice_identity_rights 層 User-donor attestation 完了専用トークン）
        # の混入で PJS 側（外部第三者）の事実を「User attestation 済み」に
        # 偽装できてしまう対称漏れを同型で塞ぐ。
        if value == _RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE:
            raise Run9ValidationError(
                f"lesson record.{field} is an external-fact field (PJS 側の事実欄); "
                f"{_RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE!r} is reserved for "
                "voice_identity_rights layer User-donor attestation — use "
                f"{_RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL!r} for an unresolved state or a concrete "
                "external description for a resolved one"
            )

    extracted_traits = data["extracted_traits"]
    if not isinstance(extracted_traits, list) or not extracted_traits:
        raise Run9ValidationError(
            f"lesson record.extracted_traits must be a non-empty list, got {extracted_traits!r}"
        )
    resolved_traits = [resolve_lesson_record_trait_alias(t) for t in extracted_traits]
    unknown_traits = set(resolved_traits) - set(PERFORMANCE_RESIDUAL_VOCAB)
    if unknown_traits:
        raise Run9ValidationError(
            f"lesson record.extracted_traits resolved to unknown trait(s) not in "
            f"PERFORMANCE_RESIDUAL_VOCAB: {sorted(unknown_traits)}"
        )

    excluded = data["explicitly_excluded_identity_traits"]
    if not isinstance(excluded, list):
        raise Run9ValidationError(
            f"lesson record.explicitly_excluded_identity_traits must be a list, got {excluded!r}"
        )
    if not all(isinstance(x, str) for x in excluded):
        raise Run9ValidationError(
            "lesson record.explicitly_excluded_identity_traits elements must all be strings, "
            f"got {excluded!r}"
        )
    missing_excluded = set(IDENTITY_EXCLUDED_TRAIT_VOCAB) - set(excluded)
    if missing_excluded:
        raise Run9ValidationError(
            "lesson record.explicitly_excluded_identity_traits must fully contain "
            f"IDENTITY_EXCLUDED_TRAIT_VOCAB — missing: {sorted(missing_excluded)}"
        )

    for field in ("rights_manifest", "provenance_manifest"):
        value = data[field]
        if not isinstance(value, str) or not value.strip():
            raise Run9ValidationError(
                f"lesson record.{field} must be a non-empty string (reference/pin), got {value!r}"
            )
        # rights_manifest/provenance_manifest は provenance 系5フィールド
        # （performance_source 等、外部第三者の"事実"を記述し
        # <UNRESOLVED_EXTERNAL> による未解決表現を許容する欄）とは別枠——
        # こちらは「実在する rights/provenance manifest への参照/pin」を
        # 保持する欄であり、未解決 placeholder の居場所ではない。
        # 第7巡 Fix 15 は User 帰属専用 <PENDING_USER_ATTESTATION> のみを
        # 拒否し代替として <UNRESOLVED_EXTERNAL> を推奨したが、代替先
        # 自体を許容すると rights_manifest/provenance_manifest の両方が
        # <UNRESOLVED_EXTERNAL> の record（＝使用可能な参照/pin を一切
        # 持たない構造的 valid record）が R9-G1 の要求証拠なしに
        # validate_lesson_record() を通ってしまう——両 sentinel を
        # fail-closed で拒否する（Codex bot レビュー PR #319 第9巡指摘
        # Fix 18、P2、採用）。値の形式（64hex sha か既存 manifest への
        # 相対パスか）は `performance_source.rights_manifest_ref` と同じ
        # 規約に倣い規定しない——非空文字列という構造的下限に留め、
        # 過剰一般化はしない。
        if value in (
            _RIGHTS_MANIFEST_PENDING_USER_ATTESTATION,
            _RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL,
        ):
            raise Run9ValidationError(
                f"lesson record.{field} must hold a genuine reference/pin to an existing "
                "rights/provenance manifest, not an unresolved placeholder — neither "
                f"{_RIGHTS_MANIFEST_PENDING_USER_ATTESTATION!r} nor "
                f"{_RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL!r} is a usable reference; if lesson "
                "generation cannot yet produce the manifest, the record cannot be structurally "
                f"issued (got {value!r})"
            )


class Run9ValidationError(ValueError):
    """Run9IdentityDomain / Run9Coords / Run9FounderGenome / RUN9 Contract の
    構築・デシリアライズ時の型・構造不正。"""


class _StrictYAMLLoader(yaml.SafeLoader):
    """`yaml.SafeLoader` を継承し、mapping ノードの重複キーを fail-closed
    拒否する（VG-E0 `models.py` `loads_strict()` と同型の fail-closed
    規約を YAML 読込にも適用する — Codex bot レビュー PR #315 第8巡指摘1
    採用）。`construct_mapping` は文書内の全ての mapping ノードへ
    （トップレベル・ネストした pin 欄 dict を含め）再帰的に呼ばれるため、
    この一箇所のオーバーライドだけで任意の深さの重複キーを検出できる。
    重複キーが無い場合の挙動は `yaml.safe_load()` と完全に同一。
    """

    def construct_mapping(self, node: Any, deep: bool = False) -> Dict[Any, Any]:
        seen: set = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise Run9ValidationError(f"duplicate key in YAML mapping: {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def normalize_signed_zero(x: float) -> float:
    """丸め後の値が負のゼロ（-0.0）であれば正準表現 +0.0 へ正規化する
    （`voice_genesis/evolution/models.py` の同名関数と同一の丸め規約 —
    coords/weights の6桁丸め結果は -0.0 になり得るため、genome_id ハッシュ・
    格納の全経路で最終防衛として正規化する）。"""
    return 0.0 if x == 0.0 else x


# ---------------------------------------------------------------------------
# Run9Coords + normalize（VG-E0 simplex.normalize() と同一意味論の独立実装 —
# 6桁丸め・残差は最大成分へ吸収・タイは anchor_order 順で決定論的に優先）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run9Coords:
    af0: float
    ritsu: float
    user: float

    def as_dict(self) -> Dict[str, float]:
        return {"af0": self.af0, "ritsu": self.ritsu, "user": self.user}


_MICRO_PER_UNIT = 1_000_000


def _require_finite_triple(af0: float, ritsu: float, user: float) -> None:
    for name, v in (("af0", af0), ("ritsu", ritsu), ("user", user)):
        if not math.isfinite(v):
            raise Run9ValidationError(f"non-finite coordinate rejected: {name}={v!r}")


# 型強制/等価比較サイトのファミリー終端宣言（Codex bot レビュー PR #315
# 第5巡（coordinate_precision/coords）→ 第6巡（run_id/experiment_id/
# claim_strength_target/ecosystem_generation/genetic_generation/
# performance_seed/parents/excluded_teacher_identities/anchor_order/
# voice_id/profile_label/skill_state/operator_id/ecosystem_role/
# identity_domain/schema/domain_id/normalization/parent_designs）で全数
# 掃討: JSON 由来の生値は `_is_strict_int()`（bool 除外 int）/
# `isinstance(x, list)`（dict のキー列挙で `list(...)` 比較をすり抜ける
# 経路を拒否）/ `isinstance(x, str)` のいずれかの厳密型検査を通ってから
# のみ等価比較・型変換に進む。本ファミリーはこの巡で終端する。


def _is_strict_int(value: Any) -> bool:
    """bool を明示的に除外した厳密 int 判定（Codex bot レビュー PR #315
    第5巡指摘1採用）: Python は `True == 1` / `6.0 == 6` が真になるため、
    `value == RUN9_COORDINATE_PRECISION` のような等価比較だけでは
    `coordinate_precision: 6.0`（float）や `coordinate_precision: true`
    のような非正準値も通過してしまう。通過を許すと、同一のはずの pinned
    domain から `content_digest()` の JSON 直列化時に異なるバイト列
    （ひいては異なる genome_id）が出る決定論欠陥になる。
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _require_valid_coord_scalar(value: Any, field: str) -> float:
    """coords 生値の型強制排除（Codex bot レビュー PR #315 第5巡指摘2
    採用）: bool でない int または有限 float のみを許可し、int は明示的に
    float へ変換する（JSON の `0`/`1` 等を許容するため）。文字列
    （例 `"0.6"`）や bool の黙った型正規化は、改ざん検出を掲げる
    `founder_genome_from_dict()` が非正準・改変された genome document を
    正典として通してしまう契約矛盾になるため拒否する。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Run9ValidationError(f"{field} must be a number (bool/str rejected), got {value!r}")
    out = float(value)
    if not math.isfinite(out):
        raise Run9ValidationError(f"{field} must be finite (NaN/inf rejected): {value!r}")
    return out


def normalize_run9_coords(af0: float, ritsu: float, user: float) -> Run9Coords:
    """(af0, ritsu, user) を Δ²（af0/ritsu/user、成分非負・合計1）上へ射影し、
    小数6桁へ丸め、合計が厳密に1.000000になるよう最大成分（クランプ前の生値
    基準）へ残差を吸収する。`voice_genesis/evolution/simplex.py`
    `normalize()` と同一の丸め規約（マイクロ単位整数演算・タイは
    `RUN9_ANCHOR_ORDER` 順で優先）を run-local に独立実装する — domain が
    異なるため import 共有はしない（本モジュール docstring 参照）。

    NaN/inf は即例外。負の生値は0へクランプする。
    """
    _require_finite_triple(af0, ritsu, user)
    raw = {"af0": af0, "ritsu": ritsu, "user": user}
    clamped = {k: max(0.0, v) for k, v in raw.items()}
    micros = {k: int(round(v * _MICRO_PER_UNIT)) for k, v in clamped.items()}
    total = sum(micros.values())
    residual = _MICRO_PER_UNIT - total

    # タイブレークは RUN9_ANCHOR_ORDER 順（af0, ritsu, user）で最初に見つかった
    # 最大値を優先する — Python の max(..., key=...) は同値タイで最初に
    # 出現した要素を返すため、raw を anchor_order 順の list として渡す。
    max_key = max(RUN9_ANCHOR_ORDER, key=lambda k: raw[k])
    micros[max_key] += residual
    if micros[max_key] < 0:
        raise Run9ValidationError(
            f"residual absorption drove the dominant component negative (raw={raw!r}); "
            "refusing to emit an invalid simplex point"
        )
    return Run9Coords(**{
        k: normalize_signed_zero(micros[k] / _MICRO_PER_UNIT) for k in ("af0", "ritsu", "user")
    })


def _validate_run9_coords_value(coords: Run9Coords) -> None:
    """coords が Δ²（af0/ritsu/user、成分非負・合計1）上の正規形（小数6桁
    丸め済み・符号付きゼロ非含有）であることを検証する。"""
    total = 0.0
    for name in RUN9_ANCHOR_ORDER:
        v = getattr(coords, name)
        if not isinstance(v, float) or isinstance(v, bool):
            raise Run9ValidationError(f"coords.{name} must be a float, got {v!r}")
        if not math.isfinite(v):
            raise Run9ValidationError(f"coords.{name} must be finite (NaN/inf rejected): {v!r}")
        if v < 0.0:
            raise Run9ValidationError(f"coords.{name} must be >= 0 (barycentric constraint): {v!r}")
        if round(v, RUN9_COORDINATE_PRECISION) != v:
            raise Run9ValidationError(
                f"coords.{name} must already be rounded to {RUN9_COORDINATE_PRECISION} decimal "
                f"places, got {v!r}"
            )
        if v == 0.0 and math.copysign(1.0, v) < 0.0:
            raise Run9ValidationError(
                f"coords.{name} must be canonical positive zero, not negative zero (-0.0), got {v!r}"
            )
        total += v
    if abs(total - 1.0) > 1e-9:
        raise Run9ValidationError(f"coords must sum to 1.000000 (barycentric constraint), got {total!r}")


# ---------------------------------------------------------------------------
# Run9IdentityDomain
# ---------------------------------------------------------------------------

_DOMAIN_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "schema", "domain_id", "anchor_order", "anchor_hashes",
    "excluded_teacher_identities", "coordinate_precision", "normalization",
    "metric_space_sha", "pin_source_candidates",
})


@dataclass(frozen=True)
class Run9IdentityDomain:
    """DESIGN_RUN9 §8 の `voicegenesis-identity-domain/1.0` run-local domain。

    `anchor_order` は `RUN9_ANCHOR_ORDER = (af0, ritsu, user)` に固定される
    （順序不変条件）。`anchor_hashes` の3キー全てに64hex sha256 が揃って
    初めて `is_pinned()` が True になる — プレースホルダ（`<PIN_BEFORE_RUN>`
    等）は未 pin 扱い。`pin_source_candidates` は任意の補助情報
    （§ domains/identity_domain_run9_v1.json 参照）で検証対象外。

    `anchor_hashes` / `pin_source_candidates` は `__post_init__` で
    `types.MappingProxyType` に凍結される（読み取り専用 `Mapping`）—
    `dataclass(frozen=True)` はトップレベル属性の再代入だけを禁止し、
    属性が指すネスト dict 自体は素の mutable dict のままだったため、
    構築後に `domain.anchor_hashes["af0"] = ...` のような in-place 書き換え
    で anchor set を差し替えても型レベルでは防げていなかった（Codex bot
    レビュー PR #315 第3巡指摘3採用）。
    """

    schema: str
    domain_id: str
    anchor_order: Tuple[str, str, str]
    anchor_hashes: Mapping[str, str]
    excluded_teacher_identities: Tuple[str, ...]
    coordinate_precision: int
    normalization: str
    metric_space_sha: str
    pin_source_candidates: Mapping[str, Any]

    def __post_init__(self) -> None:
        # frozen dataclass では `self.x = ...` が使えないため
        # `object.__setattr__` で直接代入する（dataclass 自身の凍結機構と
        # 同じ回避手段）。
        object.__setattr__(self, "anchor_hashes", types.MappingProxyType(dict(self.anchor_hashes)))
        object.__setattr__(
            self, "pin_source_candidates", types.MappingProxyType(dict(self.pin_source_candidates))
        )

    def is_pinned(self) -> bool:
        """3 anchor 全てに加え `metric_space_sha` も 64hex sha256
        （プレースホルダでない）で埋まっているときのみ True。
        `metric_space_sha` を含めるのは `content_digest()` の入力に含まれる
        ため — これを未 pin のまま genome を発行し、後から pin し直すと
        `content_digest()` ひいては genome_id が変わり、既発行の成果物が
        無効化される（将来汚染。Codex bot レビュー PR #315 指摘1採用）。"""
        for name in RUN9_ANCHOR_ORDER:
            value = self.anchor_hashes.get(name)
            if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
                return False
        if not isinstance(self.metric_space_sha, str) or not _SHA256_HEX_RE.match(self.metric_space_sha):
            return False
        return True

    def content_digest(self) -> str:
        """domain の内容ダイジェスト（正規形 JSON の sha256）。
        `build_founder()` の genome_id 計算入力に含める — anchor 未 pin の
        domain から生成した genome は毎回異なるダイジェストを持つため、
        pin 前の genome_id は「正式発行」として意味を持たない
        （DESIGN_RUN9 §22 実行順 step 3→4 の機械強制）。
        """
        canonical = _canonical_json({
            "schema": self.schema,
            "domain_id": self.domain_id,
            "anchor_order": list(self.anchor_order),
            "anchor_hashes": dict(sorted(self.anchor_hashes.items())),
            "excluded_teacher_identities": list(self.excluded_teacher_identities),
            "coordinate_precision": self.coordinate_precision,
            "normalization": self.normalization,
            "metric_space_sha": self.metric_space_sha,
        })
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_json(obj: Any) -> str:
    # allow_nan=False（Codex bot レビュー PR #318 第2巡 Fix 8 採用）:
    # 標準 json.dumps() の既定は allow_nan=True で NaN/Infinity/-Infinity を
    # 非標準の JSON リテラル（NaN/Infinity 等）として黙って出力してしまう。
    # 正規形ハッシュ（genome_id/profile_id 等）の入力に非有限値が紛れ込むと、
    # 決定論性は保たれても値そのものが JSON 標準外・下流の再パースで壊れ得る
    # 汚染源になるため、ここで fail-closed にする（呼び出し側の再帰検証との
    # 二重防御 — 詳細は run9_controlprofile.py の `_reject_non_finite()`）。
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _reject_pjs_key(*, context: str, keys: Any) -> None:
    """DESIGN_RUN9 §27 item 10「PJS coordinate is structurally impossible」:
    anchor_order / anchor_hashes / coords いずれかのキー集合に "pjs" が
    現れたら構造的に拒否する。PJS は Curriculum provider であり Identity
    anchor ではない（DESIGN_RUN9 §0/§7.4）。"""
    if isinstance(keys, (list, tuple, set, frozenset)):
        key_set = set(keys)
    elif isinstance(keys, Mapping):
        key_set = set(keys.keys())
    else:
        raise TypeError(f"unsupported keys container for pjs rejection: {type(keys).__name__}")
    if "pjs" in key_set:
        raise Run9ValidationError(
            f"{context} may not contain a 'pjs' key — PJS is an external curriculum provider, "
            "never an Identity anchor for RUN9 (DESIGN_RUN9 §0/§7.4/§27 item 10: "
            "'PJS coordinate is structurally impossible')"
        )


def build_run9_identity_domain(
    *,
    anchor_hashes: Mapping[str, str],
    metric_space_sha: str,
    pin_source_candidates: Mapping[str, Any] | None = None,
) -> Run9IdentityDomain:
    """Run9IdentityDomain を構築する唯一の経路。`anchor_order` は
    `RUN9_ANCHOR_ORDER` に固定され、呼び出し元から変更できない
    （並べ替え不可 — DESIGN_RUN9 §27 item 8）。"""
    _reject_pjs_key(context="anchor_hashes", keys=anchor_hashes)
    unknown = set(anchor_hashes.keys()) - set(RUN9_ANCHOR_ORDER)
    if unknown:
        raise Run9ValidationError(f"anchor_hashes has unknown key(s): {sorted(unknown)}")
    missing = set(RUN9_ANCHOR_ORDER) - set(anchor_hashes.keys())
    if missing:
        raise Run9ValidationError(f"anchor_hashes missing required key(s): {sorted(missing)}")
    validated_hashes: Dict[str, str] = {}
    for name in RUN9_ANCHOR_ORDER:
        v = anchor_hashes[name]
        if not isinstance(v, str) or not v:
            raise Run9ValidationError(f"anchor_hashes.{name} must be a non-empty string, got {v!r}")
        validated_hashes[name] = v

    if not isinstance(metric_space_sha, str) or not metric_space_sha:
        raise Run9ValidationError(f"metric_space_sha must be a non-empty string, got {metric_space_sha!r}")

    return Run9IdentityDomain(
        schema=SCHEMA_IDENTITY_DOMAIN,
        domain_id=RUN9_DOMAIN_ID,
        anchor_order=RUN9_ANCHOR_ORDER,
        anchor_hashes=validated_hashes,
        excluded_teacher_identities=RUN9_EXCLUDED_TEACHER_IDENTITIES,
        coordinate_precision=RUN9_COORDINATE_PRECISION,
        normalization=RUN9_NORMALIZATION,
        metric_space_sha=metric_space_sha,
        pin_source_candidates=dict(pin_source_candidates) if pin_source_candidates else {},
    )


def run9_identity_domain_from_dict(data: Any) -> Run9IdentityDomain:
    """JSON dict から Run9IdentityDomain を再構築する。fail-closed（未知
    キー拒否・欠落キーのデフォルト補完なし）。"""
    if not isinstance(data, dict):
        raise Run9ValidationError(f"identity domain document must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _DOMAIN_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"identity domain document has unknown key(s): {sorted(unknown)}")
    required = _DOMAIN_TOP_LEVEL_KEYS - {"pin_source_candidates"}
    missing = required - set(data.keys())
    if missing:
        raise Run9ValidationError(f"identity domain document missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if not isinstance(schema, str) or schema != SCHEMA_IDENTITY_DOMAIN:
        raise Run9ValidationError(f"schema must be {SCHEMA_IDENTITY_DOMAIN!r}, got {schema!r}")

    domain_id = data["domain_id"]
    if not isinstance(domain_id, str) or domain_id != RUN9_DOMAIN_ID:
        raise Run9ValidationError(f"domain_id must be {RUN9_DOMAIN_ID!r}, got {domain_id!r}")

    anchor_order_raw = data["anchor_order"]
    if not isinstance(anchor_order_raw, list):
        raise Run9ValidationError(f"anchor_order must be a list, got {type(anchor_order_raw).__name__}")
    if not all(isinstance(item, str) for item in anchor_order_raw):
        raise Run9ValidationError(f"anchor_order elements must all be strings, got {anchor_order_raw!r}")
    _reject_pjs_key(context="anchor_order", keys=anchor_order_raw)
    if tuple(anchor_order_raw) != RUN9_ANCHOR_ORDER:
        raise Run9ValidationError(
            f"anchor_order must be exactly {list(RUN9_ANCHOR_ORDER)} (fixed, no reordering allowed), "
            f"got {anchor_order_raw!r}"
        )

    anchor_hashes_raw = data["anchor_hashes"]
    if not isinstance(anchor_hashes_raw, dict):
        raise Run9ValidationError(f"anchor_hashes must be an object, got {type(anchor_hashes_raw).__name__}")
    _reject_pjs_key(context="anchor_hashes", keys=anchor_hashes_raw)
    unknown_anchor = set(anchor_hashes_raw.keys()) - set(RUN9_ANCHOR_ORDER)
    if unknown_anchor:
        raise Run9ValidationError(f"anchor_hashes has unknown key(s): {sorted(unknown_anchor)}")
    missing_anchor = set(RUN9_ANCHOR_ORDER) - set(anchor_hashes_raw.keys())
    if missing_anchor:
        raise Run9ValidationError(f"anchor_hashes missing required key(s): {sorted(missing_anchor)}")
    anchor_hashes: Dict[str, str] = {}
    for name in RUN9_ANCHOR_ORDER:
        v = anchor_hashes_raw[name]
        if not isinstance(v, str) or not v:
            raise Run9ValidationError(f"anchor_hashes.{name} must be a non-empty string, got {v!r}")
        anchor_hashes[name] = v

    excluded_raw = data["excluded_teacher_identities"]
    # isinstance(list) + 全要素 str を先行させる（Codex bot レビュー PR #315
    # 第6巡指摘2採用）: 旧実装の `list(excluded_raw) != list(...)` は、
    # `excluded_raw` が `{"pjs": 1}` のような dict でも `list(dict)` が
    # キー列挙で `["pjs"]` を返し `list(RUN9_EXCLUDED_TEACHER_IDENTITIES)`
    # （`["pjs"]`）と一致してしまう穴だった。
    if not isinstance(excluded_raw, list) or not all(isinstance(item, str) for item in excluded_raw):
        raise Run9ValidationError(
            f"excluded_teacher_identities must be a list of strings, got {excluded_raw!r}"
        )
    if list(excluded_raw) != list(RUN9_EXCLUDED_TEACHER_IDENTITIES):
        raise Run9ValidationError(
            f"excluded_teacher_identities must be exactly {list(RUN9_EXCLUDED_TEACHER_IDENTITIES)}, "
            f"got {excluded_raw!r}"
        )

    precision = data["coordinate_precision"]
    if not _is_strict_int(precision) or precision != RUN9_COORDINATE_PRECISION:
        raise Run9ValidationError(
            f"coordinate_precision must be the exact int {RUN9_COORDINATE_PRECISION!r} — bool and "
            "float variants are rejected (Python's == would otherwise accept 6.0/True as equal to "
            f"6, which breaks content_digest() determinism), got {precision!r} "
            f"({type(precision).__name__})"
        )

    normalization = data["normalization"]
    if not isinstance(normalization, str) or normalization != RUN9_NORMALIZATION:
        raise Run9ValidationError(f"normalization must be {RUN9_NORMALIZATION!r}, got {normalization!r}")

    metric_space_sha = data["metric_space_sha"]
    if not isinstance(metric_space_sha, str) or not metric_space_sha:
        raise Run9ValidationError(f"metric_space_sha must be a non-empty string, got {metric_space_sha!r}")

    pin_source_candidates_raw = data.get("pin_source_candidates", {})
    if not isinstance(pin_source_candidates_raw, dict):
        raise Run9ValidationError(
            f"pin_source_candidates must be an object, got {type(pin_source_candidates_raw).__name__}"
        )

    return Run9IdentityDomain(
        schema=schema, domain_id=domain_id, anchor_order=RUN9_ANCHOR_ORDER,
        anchor_hashes=anchor_hashes, excluded_teacher_identities=RUN9_EXCLUDED_TEACHER_IDENTITIES,
        coordinate_precision=precision, normalization=normalization, metric_space_sha=metric_space_sha,
        pin_source_candidates=dict(pin_source_candidates_raw),
    )


def _reject_duplicate_json_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """`json.loads(..., object_pairs_hook=...)` 用フック。VG-E0
    `voice_genesis/evolution/models.py` の `loads_strict()`（重複キー拒否の
    既存先例）と同型の fail-closed 規約を run-local に実装する（Codex bot
    レビュー PR #315 第8巡指摘1採用）: 標準の `json.loads` は同一 JSON
    オブジェクト内に同じキーが複数回出現しても黙って後勝ちで採用するため、
    手編集した domain document で `anchor_hashes` 内に `af0` を2回書く
    ような改ざんが検証をすり抜け得た。`object_pairs_hook` は文書内の全ての
    `{...}` ノードへボトムアップで（最も深い入れ子から順に）呼ばれるため、
    本フックをトップレベルの構築に使うだけで、任意の深さの入れ子オブジェ
    クトの重複キーも自動的に検出できる。
    """
    seen: set = set()
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise Run9ValidationError(f"duplicate key in JSON object: {key!r}")
        seen.add(key)
        result[key] = value
    return result


def _loads_strict_json(text: str) -> Any:
    """`json.loads()` 相当だが、全階層の JSON オブジェクトで重複キーを
    fail-closed 拒否する（models.py `loads_strict()` と同型の規約）。"""
    return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)


def run9_identity_domain_from_json(text: str) -> Run9IdentityDomain:
    try:
        data = _loads_strict_json(text)
    except json.JSONDecodeError as exc:
        raise Run9ValidationError(f"invalid JSON: {exc}") from exc
    return run9_identity_domain_from_dict(data)


def load_run9_identity_domain(path: Path) -> Run9IdentityDomain:
    return run9_identity_domain_from_json(Path(path).read_text(encoding="utf-8"))


def compute_file_sha256(path: Path) -> str:
    """ファイルの**実バイト列**の sha256（`sha256sum` 出力と同一値）を返す。

    RUN9 の contract pin 欄には2つの異なる sha256 規約が混在する
    （Codex bot レビュー PR #316 第8巡指摘A採用: `backbone_runtime_bundle_sha`
    の規約文言が「正規形 sha256」と「実 sha256」で混在していたため、
    `design_doc_sha256` と同一の「ファイル実バイト」規約に統一する）:

    - **ファイル実バイト規約**（本関数、`design_doc_sha256` /
      `design_revision_doc_sha256` / `backbone_runtime_bundle_sha` 等の
      大多数の pin 欄）: 対象ファイルをそのまま `sha256sum` した値。
      ファイルは人間可読な pretty-printed JSON/Markdown として保存され、
      「このファイルが手元にあるかどうか」を bit-for-bit で照合するのが
      目的。
    - **正規形（canonical）規約**（`domains/identity_domain_run9_v1.json`
      `anchor_hashes.af0` / `metric_space_sha` / `anchor_hashes.user` の
      3件 — `inputs/af0_anchor_manifest.json` / `identity_metric_space.json`
      参照）: `json.dumps(obj, sort_keys=True, ensure_ascii=False,
      separators=(",",":"))` で正規化してから sha256 する値。AF-P0 の
      `spec_sha256` 系譜（`af_spec.py canonical_json()`）と意味論を揃える
      ため、これらだけ意図的に例外としている（af0/metric_space_sha 側の
      規約を本関数へ合わせる変更は行わない — 詳細は
      af0_anchor_manifest.json の `canonicalization_method` フィールド
      参照）。`anchor_hashes.user` はこの3件の中でもさらに特殊——af0/
      metric_space_sha が「ファイル内容を正規化した」hash であるのに
      対し、user はファイルの hash ですらない。`run9_schema.
      extract_user_identity_attestation_projection()` の返り値（
      `inputs/rights_manifest.json` から導出するメモリ上の projection
      dict）を正規化してから sha256 する値であり、正規化対象がディスク上の
      どのファイルとも1対1対応しない（Codex bot レビュー PR #320 第3巡
      指摘, P2, 採用, Fix 5 — 旧文言は af0 のみを例外としており、
      Fix 1/Fix 3（PR #320 第1・2巡）で user anchor が canonical 規約
      かつ projection 由来へ移行した後も本 docstring が追随していな
      かった。再計算手順の一次レシピは
      `domains/identity_domain_run9_v1.json` `pin_source_candidates.user`
      末尾の最新 REPINNED エントリを正とする）。
    """
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _compute_canonical_pin_sha256(obj: Any) -> str:
    """`compute_file_sha256()` docstring が文書化する「正規形（canonical）
    規約」（`anchor_hashes.af0` / `metric_space_sha` / `anchor_hashes.user`
    の3 pin が使う規約）を実装するヘルパー: `json.dumps(obj, sort_keys=True,
    ensure_ascii=False, separators=(",", ":"))` で正規化してから sha256 する。

    `_canonical_json()`（本ファイル冒頭付近、`ensure_ascii=True` —
    genome_id/`Run9IdentityDomain.content_digest()` 等の直列化規約）とは
    **意図的に別物**——`ensure_ascii` の差は、対象が非 ASCII 文字を含まない
    限り出力が一致するが、`extract_user_identity_attestation_projection()`
    の返り値は `attestation.statement`（User の日本語宣誓文）を含むため、
    `_canonical_json()`（`ensure_ascii=True`）を誤用すると af0/
    metric_space_sha/user の既存 pin 値（テストの `_sha256_canonical_json()`
    ヘルパーが検証する `ensure_ascii=False` 規約で計算済み）と異なる値を
    生成してしまう。af0/metric_space_sha は現状この関数を経由せず外部で
    手動計算・pin されるのみだが（対象は本 repo 外のアーティファクトの
    形状 pin であり内容再検証は R9-G1 tooling の職務のまま — Fix 7 の
    非対称設計理由、`build_founder()` docstring 参照）、user anchor は
    Fix 7（PR #320 第5巡指摘, P1, 採用）により `build_founder()` が
    消費経路で毎回この関数を呼んで実際に再計算する。
    """
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 枝別書込境界の検証（rev 0.3 改訂A、User 外部レビュー PR #317 P1-1）
# ---------------------------------------------------------------------------


def validate_branch_write(branch: str, partition: str) -> None:
    """`branch` が `partition` へ書き込もうとする操作が
    `BRANCH_WRITABLE_PARTITIONS` の許可集合内かを検証する。範囲外
    （例: EDUCATION=TRANSFER_TECHNIQUE が TRAIT_CONTROL や IDENTITY_STATE
    へ書き込もうとする）は fail-closed で `Run9ValidationError`。将来の
    practice/education builder がこの関数を import して呼ぶことを想定する
    正本 API（builder 自体は本 PR の範囲外 — VG-L0 ハーネス実装待ち）。
    """
    if branch not in BRANCH_WRITABLE_PARTITIONS:
        raise Run9ValidationError(
            f"branch must be one of {sorted(BRANCH_WRITABLE_PARTITIONS)}, got {branch!r}"
        )
    if partition not in STATE_PARTITIONS:
        raise Run9ValidationError(
            f"partition must be one of {list(STATE_PARTITIONS)}, got {partition!r}"
        )
    writable = BRANCH_WRITABLE_PARTITIONS[branch]
    if partition not in writable:
        raise Run9ValidationError(
            f"branch {branch!r} may not write to partition {partition!r} — writable set for "
            f"{branch!r} is {list(writable)} (DESIGN_RUN9_REVISION_0.3.md 改訂A: 稽古と教育の書込"
            "境界は非対称であり、CONTROL は無介入のため書込集合は空)"
        )


_BRANCH_WRITE_POLICY_REQUIRED_KEYS: FrozenSet[str] = frozenset({
    "schema", "state_partitions", "immutable_state_partitions",
    "branch_writable_partitions", "immutable_artifacts",
})
# 機械検証対象外の人間可読な補助欄（値の内容は検証しない — pin_source_
# candidates と同様の「任意の補助情報」枠）。
_BRANCH_WRITE_POLICY_OPTIONAL_KEYS: FrozenSet[str] = frozenset({
    "description", "state_partition_meaning", "immutable_artifacts_rationale",
    "enforcement",
})
_BRANCH_WRITE_POLICY_ALLOWED_KEYS: FrozenSet[str] = (
    _BRANCH_WRITE_POLICY_REQUIRED_KEYS | _BRANCH_WRITE_POLICY_OPTIONAL_KEYS
)


def load_branch_write_policy_json(text: str) -> Dict[str, Any]:
    """`inputs/branch_write_policy.json` のテキストを重複キー拒否で読み込む
    （`load_rights_manifest_json()` と同一規約）。"""
    data = _loads_strict_json(text)
    if not isinstance(data, dict):
        raise Run9ValidationError(
            f"branch write policy document must be an object, got {type(data).__name__}"
        )
    return data


def validate_branch_write_policy_manifest(data: Mapping[str, Any]) -> None:
    """`inputs/branch_write_policy.json` の内容が、本モジュールの
    `STATE_PARTITIONS`/`IMMUTABLE_STATE_PARTITIONS`/
    `BRANCH_WRITABLE_PARTITIONS`/`BRANCH_IMMUTABLE_ARTIFACTS` 定数と
    **完全一致**することを強制する（User 外部レビュー PR #317 P1-1
    必須テスト「policy 改変で contract load または pre-run Gate が失敗
    する」の実装）。manifest は定数の「二重管理された複製」ではなく、
    定数を人間可読な形で照合可能にする従属文書という位置づけ — 一致しない
    manifest は改変（または実装とのドリフト）とみなし fail-closed で拒否
    する。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(
            f"branch write policy document must be an object, got {type(data).__name__}"
        )
    unknown = set(data.keys()) - _BRANCH_WRITE_POLICY_ALLOWED_KEYS
    if unknown:
        raise Run9ValidationError(f"branch write policy has unknown key(s): {sorted(unknown)}")
    missing = _BRANCH_WRITE_POLICY_REQUIRED_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"branch write policy missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_BRANCH_WRITE_POLICY:
        raise Run9ValidationError(
            f"branch write policy schema must be {SCHEMA_BRANCH_WRITE_POLICY!r}, got {schema!r}"
        )

    state_partitions = data["state_partitions"]
    if not isinstance(state_partitions, list) or tuple(state_partitions) != STATE_PARTITIONS:
        raise Run9ValidationError(
            f"state_partitions must be exactly {list(STATE_PARTITIONS)} (order included), "
            f"got {state_partitions!r}"
        )

    immutable_partitions = data["immutable_state_partitions"]
    if (
        not isinstance(immutable_partitions, list)
        or tuple(immutable_partitions) != IMMUTABLE_STATE_PARTITIONS
    ):
        raise Run9ValidationError(
            f"immutable_state_partitions must be exactly {list(IMMUTABLE_STATE_PARTITIONS)}, "
            f"got {immutable_partitions!r}"
        )

    writable = data["branch_writable_partitions"]
    if not isinstance(writable, dict) or set(writable.keys()) != set(BRANCH_WRITABLE_PARTITIONS.keys()):
        raise Run9ValidationError(
            f"branch_writable_partitions must have exactly keys "
            f"{sorted(BRANCH_WRITABLE_PARTITIONS.keys())}, got {writable!r}"
        )
    for branch, expected in BRANCH_WRITABLE_PARTITIONS.items():
        actual = writable[branch]
        if not isinstance(actual, list) or tuple(actual) != expected:
            raise Run9ValidationError(
                f"branch_writable_partitions[{branch!r}] must be exactly {list(expected)} "
                f"(order included), got {actual!r}"
            )

    immutable_artifacts = data["immutable_artifacts"]
    if not isinstance(immutable_artifacts, list) or tuple(immutable_artifacts) != BRANCH_IMMUTABLE_ARTIFACTS:
        raise Run9ValidationError(
            f"immutable_artifacts must be exactly {list(BRANCH_IMMUTABLE_ARTIFACTS)} (order "
            f"included), got {immutable_artifacts!r}"
        )


# ---------------------------------------------------------------------------
# PRACTICE / EDUCATION manifest 最低要件検証（rev 0.3、User 外部レビュー
# PR #317 P1-2）: split/lesson manifest の schema 欄で種別を自己宣言させ、
# 取り違え（practice manifest を education として、あるいはその逆に読ま
# せる）は schema 不一致で拒否する。
# ---------------------------------------------------------------------------

SCHEMA_PRACTICE_AUDIO_SPLIT_MANIFEST = "run9-practice-audio-split-manifest/1.0"
SCHEMA_EDUCATION_TECHNIQUE_LESSON_MANIFEST = "run9-education-technique-lesson-manifest/1.0"

# rev 0.3（Codex bot レビュー第6巡 Fix A 部分採用）: manifest 実ファイルの
# 規約パス。`practice_audio_split_manifest_sha` /
# `education_technique_lesson_manifest_sha` が PINNED へ昇格した際、この
# パスに置かれた実ファイルの sha256 が pin 値と一致し、かつ
# `validate_practice_split_manifest()`/`validate_education_lesson_manifest()`
# を通過することをテスト層が強制する（下記「層分離の境界宣言」参照）。
# `branch_write_policy.json`（`inputs/branch_write_policy.json`）と同じ
# 命名規約 — schema 識別子から機械的に導出せず、リポジトリ内の固定配置と
# して凍結する。
PRACTICE_MANIFEST_PATH = _THIS_DIR / "inputs" / "practice_audio_split_manifest.json"
EDUCATION_MANIFEST_PATH = _THIS_DIR / "inputs" / "education_technique_lesson_manifest.json"

# ---------------------------------------------------------------------------
# learning recipe manifest（RUN9 Phase 3 item 3）: rev 0.3 の枝別原則
# （DESIGN_RUN9_REVISION_0.3.md 改訂A・Codex bot レビュー第6巡 Fix B 是正
# 後の learning_recipe_sha reason）を機械可読な構造として凍結する。
# `learning_recipe_sha` は本 PR でも PENDING のまま — ここで凍結するのは
# 構造（枝別 recipe を束ねた単一 manifest・共通 seed・各枝内の二体等予算
# 宣言）+ 停止規則/試行回数/render 予算が「実行可能な形」であることの
# 型的保証（Codex bot レビュー PR #318 第2巡 Fix 7 採用 — 非空文字列 /
# 正の有限数値）。具体的な語彙・数値そのものの build（例えば
# stopping_rule を閉じた語彙へ絞る等）は VG-L0 ハーネス実装時の課題として
# 据え置く。
# ---------------------------------------------------------------------------

SCHEMA_LEARNING_RECIPE_MANIFEST = "run9-learning-recipe/1.0"

# rev 0.3 の枝別原則（PoR §8）: recipe は PRACTICE_FROM_AUDIO と
# TRANSFER_TECHNIQUE で別節に分ける（両者は情報量が本質的に異なる非対称
# フローであり、非対称性そのものが実験変数 — 単一 recipe で括らない）。
LEARNING_RECIPE_MANIFEST_PATH = _THIS_DIR / "inputs" / "learning_recipe_manifest.json"

_LEARNING_RECIPE_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "schema", "seed", "practice_recipe", "education_recipe",
})

# 各枝 recipe 節の必須キー。`equal_budget_within_arm` は bool True 必須、
# `stopping_rule`/`trial_count`/`render_budget` は「実行可能な形」まで
# 型的に検証する（Codex bot レビュー PR #318 第2巡 Fix 7 採用 — draft/
# runnable の二段 schema は作らず単一の厳密 schema とする）。具体的な
# 語彙・数値そのものの決定は VG-L0 ハーネス実装時の build 対象のまま。
#
# rev 0.3 改訂E「公平性（PoR §8）」節（DESIGN_RUN9_REVISION_0.3.md
# line 432-435 付近、Codex bot レビュー PR #318 第5巡 Fix 17 採用）:
# 「R9F-01 / R9F-02 で条件を変えない。必須共通条件（同じ PJS 素材 / 同じ
# train・validation・holdout split / 同じ探索空間 / 同じ候補生成規則 /
# 同じ試行回数 / 同じ render 予算 / 同じ評価器 / 同じ停止規則 / 同じ計算
# 予算）は各枝内で二体等予算として適用する」の9項目のうち、
# `stopping_rule`/`trial_count`/`render_budget` は Fix 7/15 で既に機械可読
# 化済み。残る5項目（探索空間・候補生成規則・評価器・計算予算・PJS素材+
# train/validation/holdout split 束縛）はこれまで recipe schema の閉じた
# キー集合に存在せず、未知キーとして拒否されるため後から足せなかった
# （Fix 17 指摘: `learning_recipe_sha` が PINNED になった後、「true の
# 等条件宣言 + trial/render 数」だけの manifest が検証を通り、公平性
# クリティカルな手続きが実装ごとに異なる比較不能実験が READY に到達し
# 得た）。以下4キーを追加する（型検証は本 PR では非空文字列の識別子/
# 記述までに留め、下位スキーマの厳密化は VG-L0 ハーネス実装時の build
# 対象のまま据え置く — Fix 7 と同じ層分離）。
_LEARNING_RECIPE_ARM_KEYS: FrozenSet[str] = frozenset({
    "equal_budget_within_arm",  # PoR §8: 各枝『内』の二体間の等予算宣言。bool True 必須
    "stopping_rule",  # 停止規則。非空文字列必須（値そのものは build 時に確定）
    "trial_count",  # 試行回数。正の厳密 int 必須（Fix 15。値そのものは build 時に確定）
    "render_budget",  # render 予算。正の有限数値必須（値そのものは build 時に確定）
    "search_space",  # rev0.3 改訂E: 同じ探索空間。非空文字列必須（Fix 17）
    "candidate_generation",  # rev0.3 改訂E: 同じ候補生成規則。非空文字列必須（Fix 17）
    "evaluator",  # rev0.3 改訂E: 同じ評価器。非空文字列必須（Fix 17）
    "compute_budget",  # rev0.3 改訂E: 同じ計算予算。非空文字列必須（Fix 17）
    "data_binding",  # rev0.3 改訂E: 同じ PJS 素材 / train・validation・holdout split 束縛。非空文字列必須（Fix 17）
})

# Fix 17 で追加した5キー（`_validate_learning_recipe_arm()` から一律に
# 「非空文字列の識別子/記述」として検証する。個々の下位スキーマ
# （例えば data_binding を sha256 pin に限定する等）は VG-L0 ハーネス
# 実装時の build 対象のまま据え置く — 本 PR は「条件が宣言されており
# 空でない」ことの機械検証までを目的とする）。
_LEARNING_RECIPE_EQUAL_CONDITION_STR_KEYS: Tuple[str, ...] = (
    "search_space", "candidate_generation", "evaluator", "compute_budget", "data_binding",
)

_LEARNING_RECIPE_ARMS: Tuple[str, str] = ("practice_recipe", "education_recipe")


def _require_non_empty_str(value: Any, *, field: str) -> str:
    """空文字列・非文字列を拒否する（Codex bot レビュー PR #318 第2巡
    Fix 7 採用）。閉じた語彙の凍結は VG-L0 ハーネス実装時の課題として
    据え置き、本 PR では「非空文字列であること」までを機械強制する。"""
    if not isinstance(value, str) or not value.strip():
        raise Run9ValidationError(f"{field} must be a non-empty string, got {value!r}")
    return value


def _require_positive_finite_number(value: Any, *, field: str) -> float:
    """bool を除外した int/float のみを許容し、有限かつ正（>0）であること
    を要求する（Codex bot レビュー PR #318 第2巡 Fix 7 採用）:
    `stopping_rule`/`trial_count`/`render_budget` は PINNED 昇格時に
    「READY 時点で実行可能な予算が凍結されている」ことを保証する必要が
    あり、None・負値・0・NaN/inf・文字列はいずれも実行不能な予算を
    静かに通してしまうため fail-closed で拒否する。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Run9ValidationError(f"{field} must be a number (bool/str/None rejected), got {value!r}")
    out = float(value)
    if not math.isfinite(out) or out <= 0.0:
        raise Run9ValidationError(f"{field} must be a finite positive number, got {value!r}")
    return out


def _require_positive_int(value: Any, *, field: str) -> int:
    """`trial_count` 専用の厳密 int 検証（Codex bot レビュー PR #318 第4巡
    Fix 15 採用、Fix 7 の是正）: `_require_positive_finite_number()` は
    bool を除く int/float を等しく許可するため、`trial_count: 1.5` の
    ような分数試行回数が PINNED recipe チェックを素通りしてしまっていた
    — 試行は実行可能な単位が整数個の離散イベントであり分数個は実行不能
    （半端な1回を実行することはできない）、かつ PoR §8 の
    `equal_budget_within_arm`（枝内の二体 Founder 間の等予算契約）は
    双方が同じ整数個の試行を消化できることを前提とするため、分数試行は
    その契約自体を掘り崩す。`_is_strict_int()` を再利用して bool を
    明示的に除外した厳密 int 判定を行う — `2.0`（値としては整数だが型が
    float）のような「一見整数に見える」値も型で拒否する（int 型のみ
    許可。`isinstance(value, int)` を素通しにすると `True`/`False` を
    誤って正の整数として通してしまうため、`_is_strict_int()` の bool
    除外が本関数でも必須）。`render_budget` は連続予算でありうるため
    対象外のまま `_require_positive_finite_number()` を使い続ける
    （本関数は `trial_count` にのみ配線する）。"""
    if not _is_strict_int(value):
        raise Run9ValidationError(
            f"{field} must be an exact int (bool/float/str/None rejected — fractional trial counts "
            f"are not executable and would undercut the equal-budget-within-arm contract), got "
            f"{value!r} ({type(value).__name__})"
        )
    if value <= 0:
        raise Run9ValidationError(f"{field} must be a positive int, got {value!r}")
    return value


def _validate_learning_recipe_arm(arm: Any, *, arm_name: str) -> None:
    if not isinstance(arm, dict):
        raise Run9ValidationError(f"learning recipe manifest.{arm_name} must be an object, got {type(arm).__name__}")
    unknown = set(arm.keys()) - _LEARNING_RECIPE_ARM_KEYS
    if unknown:
        raise Run9ValidationError(
            f"learning recipe manifest.{arm_name} has unknown key(s): {sorted(unknown)}"
        )
    missing = _LEARNING_RECIPE_ARM_KEYS - set(arm.keys())
    if missing:
        raise Run9ValidationError(
            f"learning recipe manifest.{arm_name} missing required key(s): {sorted(missing)}"
        )
    if arm["equal_budget_within_arm"] is not True:
        raise Run9ValidationError(
            f"learning recipe manifest.{arm_name}.equal_budget_within_arm must be exactly True "
            f"(PoR §8: equal budget is required within each arm, across the two founders), got "
            f"{arm['equal_budget_within_arm']!r}"
        )
    # 実行可能厳密化（Codex bot レビュー PR #318 第2巡 Fix 7 採用）: draft
    # /runnable の二段 schema は作らず、単一の厳密 schema とする。PINNED
    # 事前配線が本 validator をそのまま呼ぶ以上、READY 昇格時点で
    # stopping_rule/trial_count/render_budget が実行可能な値まで凍結
    # されていることをここで機械強制する。
    _require_non_empty_str(
        arm["stopping_rule"], field=f"learning recipe manifest.{arm_name}.stopping_rule"
    )
    # trial_count は厳密 int（Fix 15 — docstring は `_require_positive_
    # int()` 参照）。render_budget は連続予算でありうるため引き続き
    # `_require_positive_finite_number()`（正の有限 int/float）のまま。
    _require_positive_int(
        arm["trial_count"], field=f"learning recipe manifest.{arm_name}.trial_count"
    )
    _require_positive_finite_number(
        arm["render_budget"], field=f"learning recipe manifest.{arm_name}.render_budget"
    )
    # rev 0.3 改訂E「公平性（PoR §8）」節の残り5共通条件（Codex bot レビュー
    # PR #318 第5巡 Fix 17 採用 — `_LEARNING_RECIPE_ARM_KEYS` docstring
    # 参照）。stopping_rule と同じ「非空文字列」までの機械検証（具体的な
    # 語彙・形式の厳密化は VG-L0 ハーネス実装時の build 対象）。
    for field_name in _LEARNING_RECIPE_EQUAL_CONDITION_STR_KEYS:
        _require_non_empty_str(
            arm[field_name], field=f"learning recipe manifest.{arm_name}.{field_name}"
        )


def validate_learning_recipe_manifest(data: Mapping[str, Any]) -> None:
    """learning recipe manifest（`run9-learning-recipe/1.0`）の構造を検証
    する。枝別 recipe（`practice_recipe`/`education_recipe` の2節。
    CONTROL は学習 step を実行しないため recipe を持たない — PoR §4 の
    CONTROL 定義と整合）+ 共通 `seed`（`LEARNING_SEED` = 909002 固定）+
    各枝内の二体等予算宣言（`equal_budget_within_arm: true` 必須）を検証
    する。`stopping_rule`/`trial_count`/`render_budget` は具体的な語彙・
    数値そのものまでは固定しないが、「実行可能な形」であることは
    fail-closed で強制する（非空文字列 / 正の有限数値 — Codex bot レビュー
    PR #318 第2巡 Fix 7 採用。PINNED 事前配線が本 validator をそのまま
    呼ぶため、READY 昇格時点で実行不能な予算が凍結される事故を防ぐ）。

    `search_space`/`candidate_generation`/`evaluator`/`compute_budget`/
    `data_binding` の5キーも同様に非空文字列を必須とする（Codex bot
    レビュー PR #318 第5巡 Fix 17 採用、rev 0.3 改訂E「公平性（PoR §8）」
    節が定める枝内二体等条件9項目のうち、Fix 7/15 で未カバーだった残り
    5項目を機械検証可能フィールドとして追加）。各枝 recipe は
    `practice_recipe`/`education_recipe` それぞれ単一の object として
    定義され、その1つの object が該当枝の二体 Founder 双方へ共通適用
    される構造そのものが等条件を保証する（founder 別の値を持つ余地が
    schema 上そもそも存在しない）ため、既存の枝内二体一致比較は行わない
    — 個別 founder 向けの比較器を新設する必要はない。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(f"learning recipe manifest must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _LEARNING_RECIPE_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"learning recipe manifest has unknown key(s): {sorted(unknown)}")
    missing = _LEARNING_RECIPE_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"learning recipe manifest missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_LEARNING_RECIPE_MANIFEST:
        raise Run9ValidationError(
            f"learning recipe manifest schema must be exactly {SCHEMA_LEARNING_RECIPE_MANIFEST!r}, "
            f"got {schema!r}"
        )

    seed = data["seed"]
    if not _is_strict_int(seed) or seed != LEARNING_SEED:
        raise Run9ValidationError(
            f"learning recipe manifest.seed must be the exact int {LEARNING_SEED!r} (bool/float "
            f"variants rejected), got {seed!r} ({type(seed).__name__})"
        )

    for arm_name in _LEARNING_RECIPE_ARMS:
        _validate_learning_recipe_arm(data[arm_name], arm_name=arm_name)


# ---------------------------------------------------------------------------
# identity metric space manifest（RUN9 Phase 3 item 1 / Codex bot レビュー
# PR #318 第6巡 Fix 19）: `inputs/identity_metric_space.json` の閉じた形状
# 検証。旧テストはトップレベルの `schema`/`metric_version` 2ラベルしか
# 検証しておらず、repin 時に `extraction_procedure` 削除・`voiced_mask`
# 省略・ネスト型変更が素通りしていた（digest テストは「そこにある形」を
# 祝福するだけで、形そのものは検証しない）。本 validator は
# `validate_branch_write_policy_manifest()`/`validate_learning_recipe_
# manifest()` と同型の fail-closed 流儀（未知キー拒否・必須キー閉集合・
# 型検証）で、トップレベル・`extraction_procedure` の必須ネストキー・
# Fix 18 で導入した `calibration` 節の必須キーを検証する。
# ---------------------------------------------------------------------------

SCHEMA_IDENTITY_METRIC_SPACE = "run9-identity-metric-space/1.2"

IDENTITY_METRIC_SPACE_PATH = _THIS_DIR / "inputs" / "identity_metric_space.json"

# Codex bot レビュー PR #318 第13巡 Fix 33 採用（P1）: `confuser_control` を
# トップレベルキーへ追加（schema を 1.1 → 1.2 へ minor bump — 既存キーの
# 意味変更ではなくキー追加のため minor）。DESIGN_RUN9 §14 C3「PJS
# Confuser」の評価経路を復元する新設節。
_IDENTITY_METRIC_SPACE_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "schema", "metric_version", "canonicalization_method", "feature_extractor",
    "extraction_procedure", "identity_feature", "distance", "calibration",
    "reference_example", "confuser_control", "feasibility_note",
})

# Codex bot レビュー PR #318 第7巡 Fix 21 採用（P2）: `feature_extractor`/
# `identity_feature`/`distance`/`reference_example` は旧 validator がトップ
# レベルキー集合にのみ含め、内容は既存の内容照合テスト（test_phase3_*）に
# 委ねていた。しかし内容照合テストは「そこにある形」の一部だけを部分的に
# assert するに過ぎず、例えば `feature_extractor` 全体を null 化したり
# `reference_example.procedure`（参照レンダー手続きの一次記述）を削除・
# null 化する repin は、正規形 sha256 さえ pin 値と再一致すれば digest
# テストも内容照合テストの assert しない部分も素通りしてしまう。本節は
# これらの object 型トップレベルフィールド全件へ、`extraction_procedure`/
# `calibration` と同型の閉じた必須ネストキー集合 + 型検証（未知キー拒否・
# null 拒否・非空 str 強制）を追加する。純メタデータ的な str フィールド
# （`feasibility_note`）は非空 str 検証のみ追加する。
_FEATURE_EXTRACTOR_REQUIRED_KEYS: FrozenSet[str] = frozenset({
    "name", "library", "role", "version_source", "reference_implementation",
})
_FEATURE_EXTRACTOR_STR_KEYS: Tuple[str, ...] = (
    "name", "library", "role", "reference_implementation",
)
_FEATURE_EXTRACTOR_VERSION_SOURCE_REQUIRED_KEYS: FrozenSet[str] = frozenset({
    "value", "source", "note",
})

_IDENTITY_FEATURE_REQUIRED_KEYS: FrozenSet[str] = frozenset({
    "definition", "scope", "vector_source", "f0_exclusion", "aperiodicity", "level_normalization",
})
_IDENTITY_FEATURE_STR_KEYS: Tuple[str, ...] = ("definition", "scope", "vector_source")
_F0_EXCLUSION_REQUIRED_KEYS: FrozenSet[str] = frozenset({"excluded", "rationale"})
_APERIODICITY_REQUIRED_KEYS: FrozenSet[str] = frozenset({"status", "note"})

# Codex bot レビュー PR #318 第10巡 Fix 25 採用（P1）: identity feature の
# レンダーゲイン不変化を凍結する。WORLD の sp はパワー領域でレンダーゲイン
# に比例スケールするため、raw log 包絡は全 bin に約定数のオフセットが乗り、
# 稽古/教育が dynamics・全体ゲインだけ変えても Euclidean 距離が閾値超過し
# 得た（C0 はこの介入起因のエネルギー変化を校正せず、Technique の dynamics
# 軸と Identity を混同する未凍結の穴）。`level_normalization` 節へ、集約後
# ベクトルからのスカラー平均減算（gain invariance）を機械可読な形で凍結
# する。
_LEVEL_NORMALIZATION_REQUIRED_KEYS: FrozenSet[str] = frozenset({"method", "formula", "rationale"})
_LEVEL_NORMALIZATION_STR_KEYS: Tuple[str, ...] = ("method", "formula", "rationale")
# rationale がレンダーゲイン不変化（gain invariance）の理由を明文化して
# いることを最低限の証拠として要求する。
_LEVEL_NORMALIZATION_GAIN_MARKER = "ゲイン"
# formula が「集約後ベクトルからのスカラー平均減算」という凍結した式
# （feature(x) = v(x) - mean(v(x))・1）であることを要求する — per-frame
# 正規化のような別方式へこっそり repin されるのを防ぐ。
_LEVEL_NORMALIZATION_MEAN_SUBTRACTION_MARKER = "mean(v(x))"

_DISTANCE_SECTION_REQUIRED_KEYS: FrozenSet[str] = frozenset({
    "method", "definition", "properties", "note",
})
_DISTANCE_SECTION_STR_KEYS: Tuple[str, ...] = ("method", "definition", "note")

_REFERENCE_EXAMPLE_REQUIRED_KEYS: FrozenSet[str] = frozenset({"status", "procedure", "value"})
# Codex bot レビュー PR #318 第12巡 Fix 29 採用（P1）: 実測 reference の
# 循環 provenance 解消。旧 status PENDING_BIRTH_PROBE は「実測前は null、
# 実測後は書き戻して非 null にする」非対称ルールだったが、その書き戻し
# 手順自体が循環 provenance を生む欠陥だった（metric_space_sha →
# Run9IdentityDomain.content_digest() → founder の genome_id という連鎖の
# ため、実測後に本 manifest を repin すると repin 後の domain は別の
# founder を記述することになり、記録した probe の identity と manifest の
# identity が循環参照する）。本 status は手続きのみを恒久的に固定する唯一の
# 値であり、value は恒久に null（実測待ちの一時的な null ではない — この
# manifest には決して書き込まない）。実測値は出生後アーティファクト
# （RUN9_CONTRACT.yaml の post-run pin `artifact_manifest_sha` 配下の
# per-founder reference measurement record）へ記録する。非対称ルールの
# 向きは Fix 21 から反転する: 旧ルールは「PENDING 中のみ value null 許容」
# だったが、新ルールは「value は常に null が正、null 以外は拒否」
# （書き戻し企図の拒否）。
_REFERENCE_EXAMPLE_PROCEDURE_ONLY_STATUS = "PROCEDURE_ONLY_VALUE_RECORDED_IN_POST_BIRTH_ARTIFACT"

_EXTRACTION_PROCEDURE_REQUIRED_KEYS: FrozenSet[str] = frozenset({
    "f0_estimation", "frame_period_ms", "frame_period_source", "spectral_envelope",
    "fft_size_rule", "voiced_mask", "sample_rate", "sample_rate_normalization", "log_transform",
})

_F0_ESTIMATION_REQUIRED_STR_KEYS: Tuple[str, ...] = ("algorithm", "call", "note")
_SPECTRAL_ENVELOPE_REQUIRED_STR_KEYS: Tuple[str, ...] = ("algorithm", "call", "parameters", "note")
_VOICED_MASK_REQUIRED_STR_KEYS: Tuple[str, ...] = ("definition", "source")
_SAMPLE_RATE_REQUIRED_STR_KEYS: Tuple[str, ...] = ("policy", "source", "distinct_from_donor_bank_sr")
_LOG_TRANSFORM_REQUIRED_STR_KEYS: Tuple[str, ...] = ("formula", "source")

# Codex bot レビュー PR #318 第15巡 Fix 35 採用（P1）: PJS コーパスの metric
# sample rate への決定論的正規化。corpus_inventory_pjs.json によれば PJS の
# 203 WAV は全て 48000 Hz である一方、旧 extraction_procedure.sample_rate は
# 44100 Hz を pin するのみで再サンプル手続きが無く、WORLD をネイティブ適用
# すればスペクトル bin の対応周波数が食い違い、未凍結の再サンプルなら実装者
# 間で結果が再現不能になる、いずれの経路でも confuser_control の d_pjs(r)
# が壊れていた（identity_metric_space.json 旧136行付近）。着手前調査:
# feature_extractor.reference_implementation が引用する donor_bank.py:190-196
# analyze_donor_world(x, sr, ...) は x/sr を引数で受け取るだけで内部リサンプル
# を一切行わない（固定 44100Hz ロード経路は存在しない）。唯一の実装済みロード
# 経路 load_donor_24k_bytes() は soundfile 直読み + scipy.signal.resample_poly
# による有理比変換（44100→24000、up=80/down=147）だが対象は vocadito ドナー・
# 目標レートも 24000Hz で RUN9 の対象（目標 44100Hz）とは別物。
#
# Codex bot レビュー PR #318 第16巡 Fix 36 採用（P1）: 第15巡時点の rule は
# 固定 147/160 比を pin していたが、直後の applies_to が宣言する「native sr
# ≠ 44100 Hz のあらゆる入力に適用する一般規則」と矛盾していた（例: native
# 24000 Hz の入力に 147/160 を適用すると 22050 Hz へ変換され、WORLD には
# 44100 Hz として扱われて時間軸・周波数軸と identity 距離が壊れる）。本節が
# g = gcd(44100, native_sr) から up/down を native rate ごとに機械的に導出
# する一般導出式を manifest 側に pin し、147/160 は native 48000 Hz（PJS の
# 全203 WAV）に対する導出例として位置づけ直す（donor_bank.py 自体の変更は
# この PR の範囲外）。
_SAMPLE_RATE_NORMALIZATION_REQUIRED_STR_KEYS: Tuple[str, ...] = (
    "role", "investigation_finding", "rule", "applies_to", "procedure_only",
)
# rule の一般導出式マーカー（Fix 36）: native rate ごとに up/down を機械的に
# 導出する式そのもの（gcd 計算 + up/down の導出形）が現れていることを要求
# する — 固定比のみを pin して一般式を欠く旧 Fix 35 状態（あらゆる入力への
# 適用を宣言しつつ 44100/48000 専用の固定比しか書かない矛盾）への逆行を
# 拒否する。
_SAMPLE_RATE_NORMALIZATION_GENERAL_GCD_MARKER = "gcd(44100, native_sr)"
_SAMPLE_RATE_NORMALIZATION_GENERAL_UP_MARKER = "up=44100//g"
_SAMPLE_RATE_NORMALIZATION_GENERAL_DOWN_MARKER = "down=native_sr//g"
# rule の 48kHz 導出例マーカー: 一般導出式を native 48000 Hz（PJS 全203 WAV）
# へ適用した具体例として 147/160 が現れていることを要求する — 暗黙のライブ
# ラリ既定 sr 変換への逆行（ratio 未指定のまま「適切にリサンプルする」等の
# 曖昧な記述）や、導出例そのものの欠落を拒否する。
_SAMPLE_RATE_NORMALIZATION_DERIVATION_EXAMPLE_CALL_MARKER = "resample_poly(x, up=147, down=160)"
_SAMPLE_RATE_NORMALIZATION_DERIVATION_EXAMPLE_FRACTION_MARKER = "147/160"
# applies_to の一般規則性マーカー: 変換規則が PJS 特例ではなく「native sr ≠
# 44100 のあらゆる入力」に適用される一般規則であることの明文を要求する。
_SAMPLE_RATE_NORMALIZATION_GENERAL_RULE_MARKER = "あらゆる入力"
_SAMPLE_RATE_NORMALIZATION_NOT_PJS_SPECIFIC_MARKER = "PJS corpus に限定しない"

_CALIBRATION_REQUIRED_KEYS: FrozenSet[str] = frozenset({
    "status", "note", "distance_unit", "freeze_threshold", "validity_gates",
    "decision_rule", "worked_example", "source_references",
})

_CALIBRATION_DISTANCE_UNIT_STR_KEYS: Tuple[str, ...] = (
    "formula", "aggregation_unit", "reference_render_definition",
)
_CALIBRATION_FREEZE_THRESHOLD_STR_KEYS: Tuple[str, ...] = (
    "formula", "d_c0_population", "percentile_method",
)
_CALIBRATION_VALIDITY_GATES_REQUIRED_KEYS: FrozenSet[str] = frozenset({
    "policy", "c1_gate", "positive_reference_gate", "negative_reference_gate", "on_failure",
})
_CALIBRATION_GATE_STR_KEYS: Tuple[str, ...] = ("id", "condition")
_CALIBRATION_DECISION_RULE_STR_KEYS: Tuple[str, ...] = ("applies_when", "formula", "boundary")
_CALIBRATION_SOURCE_REFERENCES_STR_KEYS: Tuple[str, ...] = ("r9_g5", "holdout_freeze")

# Codex bot レビュー PR #318 第7巡 Fix 20 採用（P1）: founder 横断
# pooling 禁止の明文化と、RUN9_CONTRACT.yaml 側の実欄（Fix 20 で新設した
# `interventions.c0_replay_takes_per_founder`/`c1_sham_takes_per_founder`）
# への実参照を、フィールド名の文字列一致で機械強制する（「存在しない
# 参照」欠陥の再発防止 — 数だけ repin されて参照文言が欠落する事態を
# fail-closed で拒否する）。
_D_C0_POPULATION_POOLING_PROHIBITION_MARKER = "pooling"
_D_C0_POPULATION_FIELD_REF_MARKER = "c0_replay_takes_per_founder"
_D_C1_POPULATION_FIELD_REF_MARKER = "c1_sham_takes_per_founder"
# reference_render は founder ごとに1つに固定する（founder 横断の
# pooling/共用を禁止）— reference_render_definition 文言に "founder" が
# 独立トークンとして現れることを最低限の per-founder 明記の証拠とする。
_REFERENCE_RENDER_DEFINITION_PER_FOUNDER_MARKER = "founder"

# Codex bot レビュー PR #318 第9巡 Fix 23 採用（P1）: reference_render(F)
# が C0/C1 母集団に属するか（自己比較ゼロ距離混入）が未確定だった指摘の
# 凍結を、pooling 禁止チェックと同型のマーカー方式で機械強制する。
# reference_render(F) 自身との距離（恒等ゼロ標本）が D_C0(F)/D_C1(F) へ
# 混入すると P95 が下方へ歪み STABLE_BY_MACHINE_METRIC 側へ判定が偏る
# ため、両母集団の定義文に自己比較禁止の明文が存在することを要求する。
_SELF_COMPARISON_PROHIBITION_MARKER = "自己比較"
# reference_render_definition にも、reference が C0/C1 テイクの一員では
# ないこと（独立レンダーであること）の明文を要求する。
_REFERENCE_RENDER_NOT_A_TAKE_MARKER = "の一員ではな"

# Codex bot レビュー PR #318 第10巡 Fix 27 採用（P1）: positive/negative
# reference の生成・選定手続きの凍結。「同一 founder の再レンダー」では
# 枝・revision・制御条件・テイク・生成タイミングが未指定で、neutral C0
# レンダーを使う評価者と学習後レンダーを使う評価者で gate が反転し得た。
# positive_reference_definition が、①専用の追加テイクであること
# ②reference_render(F) 自身ではないこと ③C0 母集団のいずれのテイクでも
# ないこと ④生成タイミングが birth probe であること ⑤学習後レンダーの
# 使用が明示禁止であることを、文言マーカーとして機械強制する。
_POSITIVE_REFERENCE_DEDICATED_TAKE_MARKER = "専用"
_POSITIVE_REFERENCE_NOT_REFERENCE_RENDER_ITSELF_MARKER = "reference_render(F) 自身ではなく"
_POSITIVE_REFERENCE_NOT_C0_MEMBER_MARKER = "いずれでもない"
_POSITIVE_REFERENCE_BIRTH_PROBE_TIMING_MARKER = "birth probe"
_POSITIVE_REFERENCE_POST_LEARNING_PROHIBITION_MARKER = "学習後レンダーの使用は明示禁止"
_POSITIVE_REFERENCE_DEFINITION_MARKERS: Tuple[str, ...] = (
    _POSITIVE_REFERENCE_DEDICATED_TAKE_MARKER,
    _POSITIVE_REFERENCE_NOT_REFERENCE_RENDER_ITSELF_MARKER,
    _POSITIVE_REFERENCE_NOT_C0_MEMBER_MARKER,
    _POSITIVE_REFERENCE_BIRTH_PROBE_TIMING_MARKER,
    _POSITIVE_REFERENCE_POST_LEARNING_PROHIBITION_MARKER,
)
# negative_reference_definition も同様に、生成タイミング（birth probe）が
# 明示されていることを要求する（曖昧な「他方 founder のレンダー」だけでは
# どの時点のレンダーかが未指定のまま残るため）。
_NEGATIVE_REFERENCE_BIRTH_PROBE_TIMING_MARKER = "birth probe"

# Codex bot レビュー PR #318 第11巡 Fix 28 採用（P1）: 校正距離を
# identity_feature.level_normalization が定義する正規化 feature 基準へ
# 統一する。旧 distance_unit.formula は raw な mean_voiced_log_sp ベクトルへ
# 直接 Euclidean を適用しており、ハーネスが pin どおりに計算すると
# dynamics のみのゲイン変化が再び STABLE/SHIFTED を反転させ得た
# （metric_version 0.3 のゲイン不変の主張と矛盾）。formula が feature(...)
# 呼び出し形式であること（raw ベクトルへの直接適用への逆行を拒否）と、
# level_normalization の定義を参照する旨の明文が存在すること（定義の
# 重複記載ではなく参照で束縛されていること）の2点を機械強制する。
_DISTANCE_UNIT_FORMULA_FEATURE_CALL_MARKER = "feature("
_DISTANCE_UNIT_FORMULA_LEVEL_NORMALIZATION_REF_MARKER = "level_normalization"

# Codex bot レビュー PR #318 第12巡 Fix 30 採用（P1）: negative reference の
# 単一ソース化。旧 negative_reference_definition は「他方 founder の
# reference_render のみ」と定めつつ、同文中に「PJS は... negative reference
# としてのみ利用する」という矛盾節が残存していた（PJS は構造的に Identity
# anchor 空間から排除済みのはずなのに、直後で negative reference としての
# 利用を肯定する自己矛盾）。旧 validator は
# `_NEGATIVE_REFERENCE_BIRTH_PROBE_TIMING_MARKER`（"birth probe"）しか見て
# おらず、この内容矛盾を素通りさせていた。本節は PJS 不使用の明文マーカーを
# 要求し、旧矛盾文言への逆行を負例で拒否する。
_NEGATIVE_REFERENCE_PJS_NAME_MARKER = "PJS"
_NEGATIVE_REFERENCE_PJS_NON_USE_MARKER = "negative reference としても使用しない"
# 旧矛盾文言（削除対象）— 逆行検出用。
_NEGATIVE_REFERENCE_PJS_CONTRADICTORY_MARKER = "negative reference としてのみ利用する"

# Codex bot レビュー PR #318 第12巡 Fix 31 採用（P1）: identity_feature の
# 定義域（scope）を全 identity 評価対象レンダーへ拡張する。旧 scope は
# neutral P0/C0 レンダー限定だったが、calibration.decision_rule は
# post-practice/post-education レンダーの d(r) を要求しており、厳密実装は
# feature を計算できず、寛容実装は pinned scope を無視するしかない契約矛盾
# を抱えていた。「feature の計算可能域（全評価対象レンダー）」と「校正・
# 参照に使える母集団（neutral な r0 限定）」を区別して明文化することを
# validator が機械強制する。
_IDENTITY_FEATURE_SCOPE_EVALUATED_RENDERS_MARKER = "全ての identity 評価対象レンダー"
_IDENTITY_FEATURE_SCOPE_NEUTRAL_POPULATION_MARKER = "neutral"
_IDENTITY_FEATURE_SCOPE_DISTINCTION_MARKER = "計算可能域"

# Codex bot レビュー PR #318 第13巡 Fix 32 採用（P1）: C1 ゲートの統計的
# 欠陥の是正。C1 のアダプター効果が完全にゼロのとき D_C0(F)/D_C1(F) は
# 同一 replay-noise 分布からの独立標本であり、経験 P95 同士（尾側 vs 尾側）
# は交換可能なため、旧ゲート `P95(D_C1(F)) <= theta_cal(F)` はゼロ効果下でも
# 約1/2の確率で偽って不成立となり、on_failure の founder INVALID 化を通じて
# 全 identity 結果を不当に抑制していた。ゲート条件を分布中心（P50）vs 尾側
# （theta_cal(F) = P95(D_C0(F))）の比較へ改訂する。
_C1_GATE_CONDITION_P50_MARKER = "P50(D_C1(F)) <= theta_cal(F)"
# 旧条件文言（逆行拒否対象）。
_C1_GATE_CONDITION_OLD_P95_MARKER = "P95(D_C1(F))"
# d_c1_population が P50 の分位計算法を freeze_threshold.percentile_method
# （P95(D_C0(F)) と同一の線形補間分位）へ束縛（参照であり再定義ではない）
# していることを要求する。
_D_C1_POPULATION_PERCENTILE_METHOD_REF_MARKER = "percentile_method"
# d_c1_population が旧ゲートの統計的欠陥（ゼロ効果下での交換可能性）の
# 理由を明文していることを要求する。
_C1_GATE_EXCHANGEABLE_RATIONALE_MARKER = "交換可能"

# Codex bot レビュー PR #318 第13巡 Fix 33 採用（P1）: PJS confuser（C3）
# 評価経路の復元。DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md
# §14 C3「PJS Confuser」が要求する no-PJS-leakage 検出経路が、第12巡 Fix 30
# の PJS 全面不使用宣言により消えていた（Fix 30 は校正ゲート専用の宣言
# だったが、C3 confuser control としての限定利用まで一律に塞いでいた）。
# `identity_feature.scope` が confuser_control 節の pjs_reference を feature
# 計算可能域へ含める旨を明文していることを要求する。
_IDENTITY_FEATURE_SCOPE_CONFUSER_CONTROL_MARKER = "confuser_control"
_IDENTITY_FEATURE_SCOPE_PJS_REFERENCE_MARKER = "pjs_reference"

# confuser_control 節の閉じた必須キー集合 + 型検証（`calibration` 等と同型の
# fail-closed 流儀）。
_CONFUSER_CONTROL_REQUIRED_KEYS: FrozenSet[str] = frozenset({
    "role", "metric", "pjs_reference_definition", "evaluation",
})
_CONFUSER_CONTROL_STR_KEYS: Tuple[str, ...] = (
    "role", "metric", "pjs_reference_definition", "evaluation",
)
# role は、PJS が negative reference としては使用しない（校正ゲートの
# 母集団・reference には登場しない）が confuser control としてのみ使用する
# （本節の距離計算にのみ登場する）ことを、Fix 30 の non-use 宣言と矛盾しない
# 精密な文言で区別していることを要求する。
_CONFUSER_CONTROL_ROLE_NON_USE_AS_NEGATIVE_REFERENCE_MARKER = "negative reference としては使用しない"
_CONFUSER_CONTROL_ROLE_CONFUSER_ONLY_USE_MARKER = "confuser control としてのみ使用する"
# metric は独自の距離式を新設せず、identity_feature.level_normalization の
# 定義する正規化 feature(x) を参照（束縛）していることを要求する
# （distance_unit.formula の Fix 28 と同型の規律）。
_CONFUSER_CONTROL_METRIC_LEVEL_NORMALIZATION_REF_MARKER = "level_normalization"
_CONFUSER_CONTROL_METRIC_FEATURE_CALL_MARKER = "feature("
# evaluation は、総合スコア化・PASS/FAIL 化をしないこと（軸別 evidence のみ
# 規律）と、calibration_status(F) から独立であることの両方を明文している
# ことを要求する。
_CONFUSER_CONTROL_EVALUATION_NO_AGGREGATE_SCORE_MARKER = "PASS/FAIL"
_CONFUSER_CONTROL_EVALUATION_CALIBRATION_INDEPENDENCE_MARKER = "calibration_status"

# Codex bot レビュー PR #318 第14巡 Fix 34 採用（P1）: pjs_reference の学習前
# 決定論的凍結。旧 pjs_reference_definition は「PJS 教材コーパスから事前登録
# 手続きで固定する単一の参照レンダー/特徴」としか言っておらず、テイク
# index・digest・生成条件・決定論的集約規則を指定していなかった。選定値は
# post-run の artifact_manifest_sha 配下にしか記録されないため、評価者が
# 学習後レンダーを観察したあとで有利な PJS テイクを選定でき、
# d_pjs(r_learned) の減少有無（no-PJS-leakage evidence）を汚染し得る欠陥
# だった（identity_metric_space.json 旧136行付近）。単一テイク選択を全廃し、
# 決定論的コーパス全体集約（辞書順列挙 → 同一抽出手続き適用 → 機械的
# voiced_mask 除外 → 要素ごとの算術平均）へ置換したことを機械強制する。
_PJS_REFERENCE_DEFINITION_LEXICOGRAPHIC_ENUMERATION_MARKER = "辞書順"
_PJS_REFERENCE_DEFINITION_ARITHMETIC_MEAN_MARKER = "算術平均"
_PJS_REFERENCE_DEFINITION_CORPUS_PIN_FIELD_MARKER = "expanded_corpus_identity_sha256"
_PJS_REFERENCE_DEFINITION_VOICED_MASK_EXCLUSION_MARKER = "voiced_mask"
_PJS_REFERENCE_DEFINITION_POST_HOC_SELECTION_IMPOSSIBLE_MARKER = "事後選択"
# 旧「単一の参照レンダー」選択方式（言い換えでの再導入も含む、この具体的な
# 旧フレーズ自体）が再出現しないことを機械強制する（Fix 30 の旧矛盾文言
# 逆行拒否と同型の規律）。
_PJS_REFERENCE_DEFINITION_OLD_SINGLE_TAKE_REGRESSION_MARKER = "単一の参照レンダー"

# Codex bot レビュー PR #318 第15巡 Fix 35 採用（P1）: pjs_reference_definition
# の③特徴計算クローズが、extraction_procedure.sample_rate_normalization が
# 定める入力正規化ステップ（native 48000Hz の PJS 全203 WAV を 44100Hz へ
# 決定論的変換してから特徴計算する）を参照していることを要求する — この
# 相互参照が欠けると PJS 特徴計算が sample rate 不一致のまま行われ得る。
_PJS_REFERENCE_DEFINITION_SAMPLE_RATE_NORMALIZATION_REF_MARKER = "sample_rate_normalization"

# Codex bot レビュー PR #318 第17巡 Fix 37 採用（P1）: pjs_reference_definition
# ②列挙規則の集約対象を、既 pin（PRACTICE_MANIFEST_REQUIRED_KEYS.
# expanded_corpus_identity_sha256 → pjs_neutral.json corpus_sha256）が実際に
# 被覆するファイル集合へ限定する。着手前調査（donor_bank_lab.py
# corpus_identity_hash() line 192-227 付近）の実測により、同関数は各
# `pjsNNN/pjsNNN.lab`（音素セグメンテーション）とその対応 `pjsNNN/
# pjsNNN_song.wav` のみを対象に集約しており、speech 100 WAV・background
# 3 WAV は被覆外と判明した。旧規則（束縛したコーパス内の全音声ファイルを
# 列挙）はこの被覆外ファイルを pjs_reference の集約対象へ混入させており、
# 未 pin ファイルが corpus_identity_hash() を変えずに pjs_reference・
# no-leakage evidence を汚染し得る欠陥だった（identity_metric_space.json
# 旧143行付近）。集約対象を pin 被覆ファイル集合（`_song.wav`）へ厳密に
# 限定したことを機械強制する: ①`_song.wav` 限定への言及 ②pin 被覆の一次
# ソース（corpus_identity_hash）への参照 ③speech/background 混入禁止の
# 明文、の3マーカー必須化 + 旧「全音声ファイル列挙」規則文言への逆行拒否。
_PJS_REFERENCE_DEFINITION_SONG_WAV_SCOPE_MARKER = "_song.wav"
_PJS_REFERENCE_DEFINITION_CORPUS_IDENTITY_HASH_REF_MARKER = "corpus_identity_hash"
_PJS_REFERENCE_DEFINITION_SPEECH_BACKGROUND_EXCLUSION_MARKER = "混入禁止"
# 旧②（pin 被覆に関係なくコーパス内の音声ファイル全件を対象とする列挙規則）
# の具体的な旧フレーズ自体が再出現しないことを機械強制する（Fix 34 の
# OLD_SINGLE_TAKE_REGRESSION_MARKER と同型の逆行拒否規律）。
_PJS_REFERENCE_DEFINITION_OLD_FULL_CORPUS_ENUMERATION_REGRESSION_MARKER = (
    "束縛したコーパス内の音声ファイルを相対パスの辞書順"
)

_CALIBRATION_WORKED_EXAMPLE_STR_KEYS: Tuple[str, ...] = (
    "disclaimer", "theta_cal_derivation", "c1_gate_result", "positive_reference_gate_result",
    "negative_reference_gate_result", "calibration_status_example", "evaluated_render_outcome",
)
_CALIBRATION_WORKED_EXAMPLE_NUMBER_KEYS: Tuple[str, ...] = (
    "theta_cal", "d_c1_p50", "positive_reference_distance", "negative_reference_distance",
    "evaluated_render_distance",
)
# 実測偽装の禁止（本 repo の規律）: worked_example は合成数値例であり
# 実測ではないことを disclaimer 文言そのもので機械強制する。
_WORKED_EXAMPLE_DISCLAIMER_MARKERS: Tuple[str, ...] = ("synthetic", "実測ではない")


def _require_dict(value: Any, *, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise Run9ValidationError(f"{field} must be an object, got {type(value).__name__}")
    return value


def _validate_nested_str_keys(
    data: Mapping[str, Any],
    *,
    field: str,
    keys: Tuple[str, ...],
    allowed_keys: Optional[FrozenSet[str]] = None,
) -> None:
    # Codex bot レビュー PR #318 第9巡 Fix 24 採用（P2）: 旧実装は `keys` の
    # 存在のみを検証し、キー集合の一致（未知キー拒否）を見ていなかった
    # ため、`f0_estimation.algorithm_override: "dio"` のような契約に無い
    # キーの追加が repin だけで素通りしていた。`sample_rate`/トップレベル
    # 等で個別に行っていたキー集合比較と同型のクローズド集合検証をここへ
    # 集約し、本関数を呼ぶ全ネスト object（f0_estimation/spectral_
    # envelope/voiced_mask/distance_unit/freeze_threshold/decision_rule/
    # source_references）へ一括適用する。`keys` が非 str 型フィールド
    # （value_hz/floor_value 等、別途型検証済み）を含まない部分集合に
    # なる呼び出し元（sample_rate/log_transform）は `allowed_keys` で
    # 実際の閉集合を明示する（省略時は `keys` 自体が閉集合とみなされる）。
    permitted = set(allowed_keys) if allowed_keys is not None else set(keys)
    unknown = set(data.keys()) - permitted
    if unknown:
        raise Run9ValidationError(f"{field} has unknown key(s): {sorted(unknown)}")
    for key in keys:
        if key not in data:
            raise Run9ValidationError(f"{field} missing required key: {key!r}")
        _require_non_empty_str(data[key], field=f"{field}.{key}")


def _validate_calibration_gate(data: Any, *, field: str, definition_key: str) -> None:
    gate = _require_dict(data, field=field)
    required = set(_CALIBRATION_GATE_STR_KEYS) | {definition_key}
    unknown = set(gate.keys()) - required
    if unknown:
        raise Run9ValidationError(f"{field} has unknown key(s): {sorted(unknown)}")
    missing = required - set(gate.keys())
    if missing:
        raise Run9ValidationError(f"{field} missing required key(s): {sorted(missing)}")
    for key in required:
        _require_non_empty_str(gate[key], field=f"{field}.{key}")


def _validate_calibration_section(data: Any) -> None:
    calibration = _require_dict(data, field="identity metric space manifest.calibration")
    unknown = set(calibration.keys()) - _CALIBRATION_REQUIRED_KEYS
    if unknown:
        raise Run9ValidationError(f"calibration has unknown key(s): {sorted(unknown)}")
    missing = _CALIBRATION_REQUIRED_KEYS - set(calibration.keys())
    if missing:
        raise Run9ValidationError(f"calibration missing required key(s): {sorted(missing)}")

    _require_non_empty_str(calibration["status"], field="calibration.status")
    _require_non_empty_str(calibration["note"], field="calibration.note")

    distance_unit = _require_dict(calibration["distance_unit"], field="calibration.distance_unit")
    _validate_nested_str_keys(
        distance_unit, field="calibration.distance_unit", keys=_CALIBRATION_DISTANCE_UNIT_STR_KEYS
    )
    # Fix 20: reference_render は founder ごとに1つに固定する（他方
    # founder の reference と混同・共用しない）ことを文言レベルで強制する。
    reference_render_definition_raw = distance_unit["reference_render_definition"]
    reference_render_definition = reference_render_definition_raw.lower()
    if _REFERENCE_RENDER_DEFINITION_PER_FOUNDER_MARKER not in reference_render_definition:
        raise Run9ValidationError(
            "calibration.distance_unit.reference_render_definition must state that the reference "
            f"render is fixed per founder (expected {_REFERENCE_RENDER_DEFINITION_PER_FOUNDER_MARKER!r} "
            f"to appear), got {distance_unit['reference_render_definition']!r} (Codex bot レビュー "
            "PR #318 第7巡 Fix 20 — pooling across founders is forbidden)"
        )
    # Fix 23: reference_render(F) が C0/C1 テイクの一員ではなく独立レンダー
    # であることの明文を強制する（自己比較ゼロ距離混入の凍結）。
    if _REFERENCE_RENDER_NOT_A_TAKE_MARKER not in reference_render_definition_raw:
        raise Run9ValidationError(
            "calibration.distance_unit.reference_render_definition must state that the reference "
            f"render is not a member of the C0/C1 takes (expected {_REFERENCE_RENDER_NOT_A_TAKE_MARKER!r} "
            f"to appear), got {reference_render_definition_raw!r} (Codex bot レビュー PR #318 第9巡 "
            "Fix 23 — self-comparison zero-distance contamination is forbidden)"
        )
    # Fix 28: distance_unit.formula は identity_feature.level_normalization が
    # 定義する正規化 feature 基準（feature(x)）でなければならない。raw な
    # mean_voiced_log_sp ベクトルへの直接 Euclidean へ逆行すると、level 正規化
    # 前のゲイン変化が再び距離へ漏れ込み、metric_version 0.3 のゲイン不変の
    # 主張と矛盾する。
    distance_unit_formula = distance_unit["formula"]
    if _DISTANCE_UNIT_FORMULA_FEATURE_CALL_MARKER not in distance_unit_formula:
        raise Run9ValidationError(
            "calibration.distance_unit.formula must be expressed in terms of the normalized "
            f"feature(...) call (expected {_DISTANCE_UNIT_FORMULA_FEATURE_CALL_MARKER!r} to appear), "
            f"got {distance_unit_formula!r} (Codex bot レビュー PR #318 第11巡 Fix 28 — regressing to "
            "a raw mean_voiced_log_sp vector distance lets dynamics-only gain changes flip "
            "STABLE/SHIFTED again)"
        )
    if _DISTANCE_UNIT_FORMULA_LEVEL_NORMALIZATION_REF_MARKER not in distance_unit_formula:
        raise Run9ValidationError(
            "calibration.distance_unit.formula must reference identity_feature.level_normalization's "
            f"definition of feature(x) (expected "
            f"{_DISTANCE_UNIT_FORMULA_LEVEL_NORMALIZATION_REF_MARKER!r} to appear), got "
            f"{distance_unit_formula!r} (Codex bot レビュー PR #318 第11巡 Fix 28 — bind by reference "
            "rather than redefining feature(x) here)"
        )

    freeze_threshold = _require_dict(calibration["freeze_threshold"], field="calibration.freeze_threshold")
    _validate_nested_str_keys(
        freeze_threshold,
        field="calibration.freeze_threshold",
        keys=_CALIBRATION_FREEZE_THRESHOLD_STR_KEYS,
    )
    # Fix 20: D_C0 の pooling 禁止文言と、RUN9_CONTRACT.yaml
    # `interventions.c0_replay_takes_per_founder` へのフィールド名参照を
    # 両方強制する（数だけ repin されて禁止文言/参照が欠落する事態の防止）。
    d_c0_population = freeze_threshold["d_c0_population"]
    if _D_C0_POPULATION_POOLING_PROHIBITION_MARKER not in d_c0_population.lower():
        raise Run9ValidationError(
            "calibration.freeze_threshold.d_c0_population must state the founder-pooling "
            f"prohibition (expected {_D_C0_POPULATION_POOLING_PROHIBITION_MARKER!r} to appear — "
            "pooling would mix cross-founder identity distance into the replay noise "
            f"distribution), got {d_c0_population!r}"
        )
    if _D_C0_POPULATION_FIELD_REF_MARKER not in d_c0_population:
        raise Run9ValidationError(
            "calibration.freeze_threshold.d_c0_population must reference RUN9_CONTRACT.yaml's "
            f"{_D_C0_POPULATION_FIELD_REF_MARKER!r} field by name (dangling delegation to a "
            f"nonexistent contract field is forbidden), got {d_c0_population!r}"
        )
    # Fix 23: reference_render(F) 自身との自己比較（恒等ゼロ距離標本）の
    # D_C0(F) への混入禁止を文言レベルで強制する（未凍結のままだと P95 が
    # 下方へ歪み STABLE_BY_MACHINE_METRIC 側へ判定が偏る）。
    if _SELF_COMPARISON_PROHIBITION_MARKER not in d_c0_population:
        raise Run9ValidationError(
            "calibration.freeze_threshold.d_c0_population must state the self-comparison "
            f"contamination prohibition (expected {_SELF_COMPARISON_PROHIBITION_MARKER!r} to appear "
            "— reference_render(F) does not belong to the C0 population), got "
            f"{d_c0_population!r} (Codex bot レビュー PR #318 第9巡 Fix 23)"
        )

    validity_gates = _require_dict(calibration["validity_gates"], field="calibration.validity_gates")
    unknown_gates = set(validity_gates.keys()) - _CALIBRATION_VALIDITY_GATES_REQUIRED_KEYS
    if unknown_gates:
        raise Run9ValidationError(f"calibration.validity_gates has unknown key(s): {sorted(unknown_gates)}")
    missing_gates = _CALIBRATION_VALIDITY_GATES_REQUIRED_KEYS - set(validity_gates.keys())
    if missing_gates:
        raise Run9ValidationError(
            f"calibration.validity_gates missing required key(s): {sorted(missing_gates)}"
        )
    _require_non_empty_str(validity_gates["policy"], field="calibration.validity_gates.policy")
    _require_non_empty_str(validity_gates["on_failure"], field="calibration.validity_gates.on_failure")
    _validate_calibration_gate(
        validity_gates["c1_gate"],
        field="calibration.validity_gates.c1_gate",
        definition_key="d_c1_population",
    )
    # Fix 20: D_C1 も RUN9_CONTRACT.yaml `interventions.c1_sham_takes_per_founder`
    # へのフィールド名参照を強制する（D_C0 と対になる欠陥の同時是正）。
    d_c1_population = validity_gates["c1_gate"]["d_c1_population"]
    if _D_C1_POPULATION_FIELD_REF_MARKER not in d_c1_population:
        raise Run9ValidationError(
            "calibration.validity_gates.c1_gate.d_c1_population must reference RUN9_CONTRACT.yaml's "
            f"{_D_C1_POPULATION_FIELD_REF_MARKER!r} field by name (dangling delegation to a "
            f"nonexistent contract field is forbidden), got {d_c1_population!r}"
        )
    # Fix 23: D_C0(F) と対になる自己比較禁止チェック。
    if _SELF_COMPARISON_PROHIBITION_MARKER not in d_c1_population:
        raise Run9ValidationError(
            "calibration.validity_gates.c1_gate.d_c1_population must state the self-comparison "
            f"contamination prohibition (expected {_SELF_COMPARISON_PROHIBITION_MARKER!r} to appear "
            "— reference_render(F) does not belong to the C1 population), got "
            f"{d_c1_population!r} (Codex bot レビュー PR #318 第9巡 Fix 23)"
        )
    # Fix 32: C1 ゲート条件が分布中心（P50）vs 尾側（theta_cal(F)）の比較で
    # あることを機械強制し、統計的欠陥のあった旧尾側 vs 尾側比較
    # （P95(D_C1(F))）への逆行を拒否する。
    c1_condition = validity_gates["c1_gate"]["condition"]
    if _C1_GATE_CONDITION_P50_MARKER not in c1_condition:
        raise Run9ValidationError(
            "calibration.validity_gates.c1_gate.condition must be the median-vs-tail comparison "
            f"(expected {_C1_GATE_CONDITION_P50_MARKER!r} to appear), got {c1_condition!r} (Codex bot "
            "レビュー PR #318 第13巡 Fix 32 — the old tail-vs-tail comparison P95(D_C1(F)) is "
            "exchangeable with P95(D_C0(F)) under a zero adapter effect and spuriously fails ~50% of "
            "the time)"
        )
    if _C1_GATE_CONDITION_OLD_P95_MARKER in c1_condition:
        raise Run9ValidationError(
            "calibration.validity_gates.c1_gate.condition must not regress to the old tail-vs-tail "
            f"phrase {_C1_GATE_CONDITION_OLD_P95_MARKER!r} (Codex bot レビュー PR #318 第13巡 Fix 32), "
            f"got {c1_condition!r}"
        )
    # Fix 32: d_c1_population が P50 の分位計算法を percentile_method へ
    # 参照束縛していること、および旧ゲートの統計的欠陥（交換可能性）の理由を
    # 明文していることを要求する。
    if _D_C1_POPULATION_PERCENTILE_METHOD_REF_MARKER not in d_c1_population:
        raise Run9ValidationError(
            "calibration.validity_gates.c1_gate.d_c1_population must bind P50's quantile method to "
            f"freeze_threshold.percentile_method by reference (expected "
            f"{_D_C1_POPULATION_PERCENTILE_METHOD_REF_MARKER!r} to appear), got {d_c1_population!r} "
            "(Codex bot レビュー PR #318 第13巡 Fix 32)"
        )
    if _C1_GATE_EXCHANGEABLE_RATIONALE_MARKER not in d_c1_population:
        raise Run9ValidationError(
            "calibration.validity_gates.c1_gate.d_c1_population must state the zero-effect "
            f"exchangeability rationale for the P50 revision (expected "
            f"{_C1_GATE_EXCHANGEABLE_RATIONALE_MARKER!r} to appear), got {d_c1_population!r} "
            "(Codex bot レビュー PR #318 第13巡 Fix 32)"
        )
    _validate_calibration_gate(
        validity_gates["positive_reference_gate"],
        field="calibration.validity_gates.positive_reference_gate",
        definition_key="positive_reference_definition",
    )
    # Fix 27: positive reference の生成・選定手続きの凍結を文言マーカーで
    # 機械強制する（docstring 参照）。
    positive_reference_definition = validity_gates["positive_reference_gate"][
        "positive_reference_definition"
    ]
    for marker in _POSITIVE_REFERENCE_DEFINITION_MARKERS:
        if marker not in positive_reference_definition:
            raise Run9ValidationError(
                "calibration.validity_gates.positive_reference_gate.positive_reference_definition "
                f"must state {marker!r} (Codex bot レビュー PR #318 第10巡 Fix 27 — pin the "
                f"positive-reference render selection procedure), got "
                f"{positive_reference_definition!r}"
            )
    _validate_calibration_gate(
        validity_gates["negative_reference_gate"],
        field="calibration.validity_gates.negative_reference_gate",
        definition_key="negative_reference_definition",
    )
    # Fix 27: negative reference にも生成タイミング（birth probe）の明示を
    # 要求する（同型の曖昧さ再発防止）。
    negative_reference_definition = validity_gates["negative_reference_gate"][
        "negative_reference_definition"
    ]
    if _NEGATIVE_REFERENCE_BIRTH_PROBE_TIMING_MARKER not in negative_reference_definition:
        raise Run9ValidationError(
            "calibration.validity_gates.negative_reference_gate.negative_reference_definition must "
            f"state its generation timing (expected "
            f"{_NEGATIVE_REFERENCE_BIRTH_PROBE_TIMING_MARKER!r} to appear), got "
            f"{negative_reference_definition!r} (Codex bot レビュー PR #318 第10巡 Fix 27)"
        )
    # Fix 30: negative reference の単一ソース化 — PJS への言及がある場合は
    # 必ず「negative reference としても使用しない」旨でなければならず、
    # 旧矛盾文言（「negative reference としてのみ利用する」）への逆行を
    # 拒否する。
    if _NEGATIVE_REFERENCE_PJS_NAME_MARKER in negative_reference_definition:
        if _NEGATIVE_REFERENCE_PJS_NON_USE_MARKER not in negative_reference_definition:
            raise Run9ValidationError(
                "calibration.validity_gates.negative_reference_gate.negative_reference_definition "
                f"mentions PJS but does not state the non-use marker (expected "
                f"{_NEGATIVE_REFERENCE_PJS_NON_USE_MARKER!r} to appear), got "
                f"{negative_reference_definition!r} (Codex bot レビュー PR #318 第12巡 Fix 30 — PJS "
                "is structurally excluded from the Identity anchor space and must not be used as a "
                "negative reference either)"
            )
    if _NEGATIVE_REFERENCE_PJS_CONTRADICTORY_MARKER in negative_reference_definition:
        raise Run9ValidationError(
            "calibration.validity_gates.negative_reference_gate.negative_reference_definition must "
            f"not contain the old contradictory phrase {_NEGATIVE_REFERENCE_PJS_CONTRADICTORY_MARKER!r} "
            f"(PJS being 'structurally excluded' and then 'used only as a negative reference' in the "
            f"same definition is self-contradictory), got {negative_reference_definition!r} (Codex bot "
            "レビュー PR #318 第12巡 Fix 30)"
        )

    decision_rule = _require_dict(calibration["decision_rule"], field="calibration.decision_rule")
    _validate_nested_str_keys(
        decision_rule, field="calibration.decision_rule", keys=_CALIBRATION_DECISION_RULE_STR_KEYS
    )

    source_references = _require_dict(
        calibration["source_references"], field="calibration.source_references"
    )
    _validate_nested_str_keys(
        source_references,
        field="calibration.source_references",
        keys=_CALIBRATION_SOURCE_REFERENCES_STR_KEYS,
    )

    worked_example = _require_dict(calibration["worked_example"], field="calibration.worked_example")
    required_worked_keys = set(_CALIBRATION_WORKED_EXAMPLE_STR_KEYS) | set(
        _CALIBRATION_WORKED_EXAMPLE_NUMBER_KEYS
    ) | {"d_c0_samples"}
    unknown_worked = set(worked_example.keys()) - required_worked_keys
    if unknown_worked:
        raise Run9ValidationError(f"calibration.worked_example has unknown key(s): {sorted(unknown_worked)}")
    missing_worked = required_worked_keys - set(worked_example.keys())
    if missing_worked:
        raise Run9ValidationError(
            f"calibration.worked_example missing required key(s): {sorted(missing_worked)}"
        )
    for key in _CALIBRATION_WORKED_EXAMPLE_STR_KEYS:
        _require_non_empty_str(worked_example[key], field=f"calibration.worked_example.{key}")
    disclaimer = worked_example["disclaimer"].lower()
    if not any(marker.lower() in disclaimer for marker in _WORKED_EXAMPLE_DISCLAIMER_MARKERS):
        raise Run9ValidationError(
            "calibration.worked_example.disclaimer must mark the example as a synthetic "
            f"illustration (expected one of {_WORKED_EXAMPLE_DISCLAIMER_MARKERS!r} to appear), "
            f"got {worked_example['disclaimer']!r} (実測偽装の禁止 — 本 repo の規律)"
        )
    for key in _CALIBRATION_WORKED_EXAMPLE_NUMBER_KEYS:
        _require_positive_finite_number(worked_example[key], field=f"calibration.worked_example.{key}")
    d_c0_samples = worked_example["d_c0_samples"]
    if not isinstance(d_c0_samples, list) or not d_c0_samples:
        raise Run9ValidationError(
            f"calibration.worked_example.d_c0_samples must be a non-empty list, got {d_c0_samples!r}"
        )
    for i, sample in enumerate(d_c0_samples):
        _require_positive_finite_number(sample, field=f"calibration.worked_example.d_c0_samples[{i}]")


def _validate_extraction_procedure(data: Any) -> None:
    extraction = _require_dict(data, field="identity metric space manifest.extraction_procedure")
    unknown = set(extraction.keys()) - _EXTRACTION_PROCEDURE_REQUIRED_KEYS
    if unknown:
        raise Run9ValidationError(f"extraction_procedure has unknown key(s): {sorted(unknown)}")
    missing = _EXTRACTION_PROCEDURE_REQUIRED_KEYS - set(extraction.keys())
    if missing:
        raise Run9ValidationError(f"extraction_procedure missing required key(s): {sorted(missing)}")

    f0_estimation = _require_dict(extraction["f0_estimation"], field="extraction_procedure.f0_estimation")
    _validate_nested_str_keys(
        f0_estimation, field="extraction_procedure.f0_estimation", keys=_F0_ESTIMATION_REQUIRED_STR_KEYS
    )

    _require_positive_finite_number(
        extraction["frame_period_ms"], field="extraction_procedure.frame_period_ms"
    )
    _require_non_empty_str(
        extraction["frame_period_source"], field="extraction_procedure.frame_period_source"
    )

    spectral_envelope = _require_dict(
        extraction["spectral_envelope"], field="extraction_procedure.spectral_envelope"
    )
    _validate_nested_str_keys(
        spectral_envelope,
        field="extraction_procedure.spectral_envelope",
        keys=_SPECTRAL_ENVELOPE_REQUIRED_STR_KEYS,
    )

    _require_non_empty_str(extraction["fft_size_rule"], field="extraction_procedure.fft_size_rule")

    voiced_mask = _require_dict(extraction["voiced_mask"], field="extraction_procedure.voiced_mask")
    _validate_nested_str_keys(
        voiced_mask, field="extraction_procedure.voiced_mask", keys=_VOICED_MASK_REQUIRED_STR_KEYS
    )

    sample_rate = _require_dict(extraction["sample_rate"], field="extraction_procedure.sample_rate")
    required_sample_rate_keys = set(_SAMPLE_RATE_REQUIRED_STR_KEYS) | {"value_hz"}
    unknown_sr = set(sample_rate.keys()) - required_sample_rate_keys
    if unknown_sr:
        raise Run9ValidationError(f"extraction_procedure.sample_rate has unknown key(s): {sorted(unknown_sr)}")
    missing_sr = required_sample_rate_keys - set(sample_rate.keys())
    if missing_sr:
        raise Run9ValidationError(
            f"extraction_procedure.sample_rate missing required key(s): {sorted(missing_sr)}"
        )
    value_hz = sample_rate["value_hz"]
    if not _is_strict_int(value_hz) or value_hz <= 0:
        raise Run9ValidationError(
            f"extraction_procedure.sample_rate.value_hz must be a positive exact int (bool/float "
            f"rejected), got {value_hz!r} ({type(value_hz).__name__})"
        )
    _validate_nested_str_keys(
        sample_rate,
        field="extraction_procedure.sample_rate",
        keys=_SAMPLE_RATE_REQUIRED_STR_KEYS,
        allowed_keys=frozenset(required_sample_rate_keys),
    )

    # Fix 35: sample_rate_normalization — native sr ≠ 44100 Hz の入力（PJS
    # corpus 等）への決定論的変換規則の存在・決定論性・一般規則性を機械
    # 強制する。旧「変換規則なし」状態（本キー欠落）は
    # _EXTRACTION_PROCEDURE_REQUIRED_KEYS の必須キー化により既に拒否される。
    sample_rate_normalization = _require_dict(
        extraction["sample_rate_normalization"], field="extraction_procedure.sample_rate_normalization"
    )
    _validate_nested_str_keys(
        sample_rate_normalization,
        field="extraction_procedure.sample_rate_normalization",
        keys=_SAMPLE_RATE_NORMALIZATION_REQUIRED_STR_KEYS,
    )
    sr_norm_rule = sample_rate_normalization["rule"]
    for marker in (
        _SAMPLE_RATE_NORMALIZATION_GENERAL_GCD_MARKER,
        _SAMPLE_RATE_NORMALIZATION_GENERAL_UP_MARKER,
        _SAMPLE_RATE_NORMALIZATION_GENERAL_DOWN_MARKER,
    ):
        if marker not in sr_norm_rule:
            raise Run9ValidationError(
                "extraction_procedure.sample_rate_normalization.rule must state the general "
                f"per-native-rate ratio derivation formula (expected {marker!r} to appear — a rule "
                "that pins a single fixed ratio (e.g. 147/160) without the derivation formula would "
                "silently misconvert any native rate other than the one the fixed ratio was derived "
                "for, contradicting the adjacent applies_to claim that this rule applies to every "
                f"native sr != 44100 Hz input), got {sr_norm_rule!r} (Codex bot レビュー PR #318 "
                "第16巡 Fix 36)"
            )
    for marker in (
        _SAMPLE_RATE_NORMALIZATION_DERIVATION_EXAMPLE_CALL_MARKER,
        _SAMPLE_RATE_NORMALIZATION_DERIVATION_EXAMPLE_FRACTION_MARKER,
    ):
        if marker not in sr_norm_rule:
            raise Run9ValidationError(
                "extraction_procedure.sample_rate_normalization.rule must state the worked 48000 Hz "
                f"derivation example (expected {marker!r} to appear — an implicit or unspecified "
                "resample procedure would make results reproducible only by accident), "
                f"got {sr_norm_rule!r} (Codex bot レビュー PR #318 第15巡 Fix 35 / 第16巡 Fix 36)"
            )
    sr_norm_applies_to = sample_rate_normalization["applies_to"]
    for marker in (
        _SAMPLE_RATE_NORMALIZATION_GENERAL_RULE_MARKER,
        _SAMPLE_RATE_NORMALIZATION_NOT_PJS_SPECIFIC_MARKER,
    ):
        if marker not in sr_norm_applies_to:
            raise Run9ValidationError(
                f"extraction_procedure.sample_rate_normalization.applies_to must state {marker!r} "
                "(Codex bot レビュー PR #318 第15巡 Fix 35 — the conversion rule must be a general "
                "rule for any input whose native sr differs from 44100 Hz, not a PJS-only special "
                f"case), got {sr_norm_applies_to!r}"
            )

    log_transform = _require_dict(extraction["log_transform"], field="extraction_procedure.log_transform")
    required_log_keys = set(_LOG_TRANSFORM_REQUIRED_STR_KEYS) | {"floor_value"}
    unknown_log = set(log_transform.keys()) - required_log_keys
    if unknown_log:
        raise Run9ValidationError(f"extraction_procedure.log_transform has unknown key(s): {sorted(unknown_log)}")
    missing_log = required_log_keys - set(log_transform.keys())
    if missing_log:
        raise Run9ValidationError(
            f"extraction_procedure.log_transform missing required key(s): {sorted(missing_log)}"
        )
    _require_positive_finite_number(
        log_transform["floor_value"], field="extraction_procedure.log_transform.floor_value"
    )
    _validate_nested_str_keys(
        log_transform,
        field="extraction_procedure.log_transform",
        keys=_LOG_TRANSFORM_REQUIRED_STR_KEYS,
        allowed_keys=frozenset(required_log_keys),
    )


def _validate_feature_extractor(data: Any) -> None:
    fe = _require_dict(data, field="identity metric space manifest.feature_extractor")
    unknown = set(fe.keys()) - _FEATURE_EXTRACTOR_REQUIRED_KEYS
    if unknown:
        raise Run9ValidationError(f"feature_extractor has unknown key(s): {sorted(unknown)}")
    missing = _FEATURE_EXTRACTOR_REQUIRED_KEYS - set(fe.keys())
    if missing:
        raise Run9ValidationError(f"feature_extractor missing required key(s): {sorted(missing)}")
    for key in _FEATURE_EXTRACTOR_STR_KEYS:
        _require_non_empty_str(fe[key], field=f"feature_extractor.{key}")

    version_source = _require_dict(fe["version_source"], field="feature_extractor.version_source")
    unknown_vs = set(version_source.keys()) - _FEATURE_EXTRACTOR_VERSION_SOURCE_REQUIRED_KEYS
    if unknown_vs:
        raise Run9ValidationError(f"feature_extractor.version_source has unknown key(s): {sorted(unknown_vs)}")
    missing_vs = _FEATURE_EXTRACTOR_VERSION_SOURCE_REQUIRED_KEYS - set(version_source.keys())
    if missing_vs:
        raise Run9ValidationError(
            f"feature_extractor.version_source missing required key(s): {sorted(missing_vs)}"
        )
    for key in _FEATURE_EXTRACTOR_VERSION_SOURCE_REQUIRED_KEYS:
        _require_non_empty_str(version_source[key], field=f"feature_extractor.version_source.{key}")


def _validate_identity_feature(data: Any) -> None:
    identity_feature = _require_dict(data, field="identity metric space manifest.identity_feature")
    unknown = set(identity_feature.keys()) - _IDENTITY_FEATURE_REQUIRED_KEYS
    if unknown:
        raise Run9ValidationError(f"identity_feature has unknown key(s): {sorted(unknown)}")
    missing = _IDENTITY_FEATURE_REQUIRED_KEYS - set(identity_feature.keys())
    if missing:
        raise Run9ValidationError(f"identity_feature missing required key(s): {sorted(missing)}")
    for key in _IDENTITY_FEATURE_STR_KEYS:
        _require_non_empty_str(identity_feature[key], field=f"identity_feature.{key}")

    # Fix 31: scope はここで「feature の計算可能域（全 identity 評価対象
    # レンダー）」と「校正・参照に使える母集団（neutral な r0 限定）」の
    # 両方を明文で区別していなければならない — 旧 P0/C0 限定 scope への
    # 無断退行（calibration.decision_rule が要求する post-practice/
    # post-education レンダーの d(r) が再び計算不能になる）を防ぐ。
    scope = identity_feature["scope"]
    for marker in (
        _IDENTITY_FEATURE_SCOPE_EVALUATED_RENDERS_MARKER,
        _IDENTITY_FEATURE_SCOPE_NEUTRAL_POPULATION_MARKER,
        _IDENTITY_FEATURE_SCOPE_DISTINCTION_MARKER,
    ):
        if marker not in scope:
            raise Run9ValidationError(
                f"identity_feature.scope must state {marker!r} (Codex bot レビュー PR #318 第12巡 "
                "Fix 31 — the feature's computable domain must cover every identity-evaluation "
                "render that calibration.decision_rule requires d(r) for, while the calibration "
                "population and reference renders remain neutral-only; both must be stated and kept "
                f"distinct), got {scope!r}"
            )
    # Fix 33: scope は confuser_control 節の pjs_reference も feature の計算
    # 可能域へ含める旨を明文していなければならない（DESIGN_RUN9 §14 C3 の
    # 評価経路が feature を計算できる対象を確保するため）。
    for marker in (
        _IDENTITY_FEATURE_SCOPE_CONFUSER_CONTROL_MARKER,
        _IDENTITY_FEATURE_SCOPE_PJS_REFERENCE_MARKER,
    ):
        if marker not in scope:
            raise Run9ValidationError(
                f"identity_feature.scope must state {marker!r} (Codex bot レビュー PR #318 第13巡 "
                "Fix 33 — the feature's computable domain must also cover the confuser_control "
                "section's pjs_reference render, without folding it into the neutral calibration "
                f"population/reference), got {scope!r}"
            )

    f0_exclusion = _require_dict(identity_feature["f0_exclusion"], field="identity_feature.f0_exclusion")
    unknown_f0 = set(f0_exclusion.keys()) - _F0_EXCLUSION_REQUIRED_KEYS
    if unknown_f0:
        raise Run9ValidationError(f"identity_feature.f0_exclusion has unknown key(s): {sorted(unknown_f0)}")
    missing_f0 = _F0_EXCLUSION_REQUIRED_KEYS - set(f0_exclusion.keys())
    if missing_f0:
        raise Run9ValidationError(
            f"identity_feature.f0_exclusion missing required key(s): {sorted(missing_f0)}"
        )
    if not isinstance(f0_exclusion["excluded"], bool):
        raise Run9ValidationError(
            f"identity_feature.f0_exclusion.excluded must be a bool, got {f0_exclusion['excluded']!r} "
            f"({type(f0_exclusion['excluded']).__name__})"
        )
    _require_non_empty_str(f0_exclusion["rationale"], field="identity_feature.f0_exclusion.rationale")

    aperiodicity = _require_dict(identity_feature["aperiodicity"], field="identity_feature.aperiodicity")
    unknown_ap = set(aperiodicity.keys()) - _APERIODICITY_REQUIRED_KEYS
    if unknown_ap:
        raise Run9ValidationError(f"identity_feature.aperiodicity has unknown key(s): {sorted(unknown_ap)}")
    missing_ap = _APERIODICITY_REQUIRED_KEYS - set(aperiodicity.keys())
    if missing_ap:
        raise Run9ValidationError(
            f"identity_feature.aperiodicity missing required key(s): {sorted(missing_ap)}"
        )
    _require_non_empty_str(aperiodicity["status"], field="identity_feature.aperiodicity.status")
    _require_non_empty_str(aperiodicity["note"], field="identity_feature.aperiodicity.note")

    # Fix 25: level_normalization 節（gain invariance の凍結）を検証する。
    level_normalization = _require_dict(
        identity_feature["level_normalization"], field="identity_feature.level_normalization"
    )
    _validate_nested_str_keys(
        level_normalization,
        field="identity_feature.level_normalization",
        keys=_LEVEL_NORMALIZATION_STR_KEYS,
    )
    rationale = level_normalization["rationale"]
    if _LEVEL_NORMALIZATION_GAIN_MARKER not in rationale:
        raise Run9ValidationError(
            "identity_feature.level_normalization.rationale must state the render-gain invariance "
            f"reasoning (expected {_LEVEL_NORMALIZATION_GAIN_MARKER!r} to appear), got {rationale!r} "
            "(Codex bot レビュー PR #318 第10巡 Fix 25 — WORLD の sp はパワー領域でレンダーゲインに "
            "比例スケールするため、raw log 包絡は全 bin に約定数のオフセットが乗る)"
        )
    formula = level_normalization["formula"]
    if _LEVEL_NORMALIZATION_MEAN_SUBTRACTION_MARKER not in formula:
        raise Run9ValidationError(
            "identity_feature.level_normalization.formula must be the frozen scalar-mean-subtraction "
            f"form (expected {_LEVEL_NORMALIZATION_MEAN_SUBTRACTION_MARKER!r} to appear), got "
            f"{formula!r} (Codex bot レビュー PR #318 第10巡 Fix 25 — per-frame 正規化等への無断置換 "
            "を防ぐ)"
        )


def _validate_distance_section(data: Any) -> None:
    distance = _require_dict(data, field="identity metric space manifest.distance")
    unknown = set(distance.keys()) - _DISTANCE_SECTION_REQUIRED_KEYS
    if unknown:
        raise Run9ValidationError(f"distance has unknown key(s): {sorted(unknown)}")
    missing = _DISTANCE_SECTION_REQUIRED_KEYS - set(distance.keys())
    if missing:
        raise Run9ValidationError(f"distance missing required key(s): {sorted(missing)}")
    for key in _DISTANCE_SECTION_STR_KEYS:
        _require_non_empty_str(distance[key], field=f"distance.{key}")

    properties = distance["properties"]
    if not isinstance(properties, list) or not properties:
        raise Run9ValidationError(f"distance.properties must be a non-empty list, got {properties!r}")
    for i, prop in enumerate(properties):
        _require_non_empty_str(prop, field=f"distance.properties[{i}]")


def _validate_reference_example(data: Any) -> None:
    """Codex bot レビュー PR #318 第12巡 Fix 29 採用（P1）: 実測 reference の
    循環 provenance を解消するため、reference_example は procedure-only の
    恒久 pin として検証する。status は凍結した唯一の値
    （`_REFERENCE_EXAMPLE_PROCEDURE_ONLY_STATUS`）と厳密一致し、value は
    常に null でなければならない（非対称ルールを Fix 21 から反転: 旧ルールは
    「PENDING 中のみ null 許容・それ以外は非 null 必須」だったが、新ルールは
    「常に null が正・null 以外は拒否」——実測値をこの founder-defining pin
    へ書き戻す企図そのものを拒否する）。実測値は
    RUN9_CONTRACT.yaml の post-run pin `artifact_manifest_sha` 配下の
    post-birth artifact（測定記録側）に記録し、本 manifest（founder 定義側）
    は手続きのみを保持する片方向の provenance を維持する。
    """
    ref = _require_dict(data, field="identity metric space manifest.reference_example")
    unknown = set(ref.keys()) - _REFERENCE_EXAMPLE_REQUIRED_KEYS
    if unknown:
        raise Run9ValidationError(f"reference_example has unknown key(s): {sorted(unknown)}")
    missing = _REFERENCE_EXAMPLE_REQUIRED_KEYS - set(ref.keys())
    if missing:
        raise Run9ValidationError(f"reference_example missing required key(s): {sorted(missing)}")
    _require_non_empty_str(ref["status"], field="reference_example.status")
    _require_non_empty_str(ref["procedure"], field="reference_example.procedure")

    status = ref["status"]
    if status != _REFERENCE_EXAMPLE_PROCEDURE_ONLY_STATUS:
        raise Run9ValidationError(
            f"reference_example.status must be exactly {_REFERENCE_EXAMPLE_PROCEDURE_ONLY_STATUS!r} "
            "(Codex bot レビュー PR #318 第12巡 Fix 29 — this manifest is a procedure-only pin that "
            f"never records the measured birth-probe reference value), got {status!r}"
        )

    value = ref["value"]
    if value is not None:
        raise Run9ValidationError(
            "reference_example.value must remain permanently null — writing the measured "
            "birth-probe reference value back into this manifest would repin metric_space_sha and, "
            "via Run9IdentityDomain.content_digest(), change the founder's genome_id, creating a "
            "circular provenance between the recorded probe's identity and the manifest that defines "
            "that identity (Codex bot レビュー PR #318 第12巡 Fix 29). Record the measured value in "
            "the post-birth artifact bound under RUN9_CONTRACT.yaml's artifact_manifest_sha instead, "
            f"got {value!r}"
        )


def _validate_confuser_control_section(data: Any) -> None:
    """Codex bot レビュー PR #318 第13巡 Fix 33 採用（P1）: `confuser_control`
    節（DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md §14 C3「PJS
    Confuser」の実装復元）の閉じた形状を検証する。role/metric/
    pjs_reference_definition/evaluation の4キー閉集合 + 非空 str 型検証に
    加え、以下の意味論マーカーを機械強制する: ①role は「negative reference
    としては使用しない」と「confuser control としてのみ使用する」の両方を
    区別して明文（Fix 30 の校正ゲート専用 non-use 宣言と矛盾しない精密化）
    ②metric は identity_feature.level_normalization の定義する feature(x)
    を参照（独自距離式を新設しない）③evaluation は総合スコア化・PASS/FAIL
    化をしないこと（軸別 evidence のみ規律）と calibration_status(F) から
    独立であることの両方を明文。

    第14巡 Fix 34（P1）: pjs_reference_definition の学習前決定論的凍結を
    追加検証する。単一テイク選択（旧「単一の参照レンダー/特徴」）への逆行を
    拒否し、決定論的コーパス集約（辞書順列挙・expanded_corpus_identity_
    sha256 pin フィールドへの入力束縛・voiced_mask による機械的除外・要素
    ごとの算術平均・学習後の事後選択が構造的に不可能である旨の明文）の
    5マーカーを機械強制する。

    第17巡 Fix 37（P1）: ②列挙規則の集約対象を、既 pin（corpus_identity_
    hash()）が実際に被覆するファイル集合（`_song.wav`）へ限定したことを
    追加検証する。旧「コーパス内の全音声ファイル列挙」（speech 100 WAV・
    background 3 WAV を含む pin 被覆外ファイルの混入を許した規則）への
    逆行を拒否し、`_song.wav` 限定・corpus_identity_hash() への被覆一次
    ソース参照・speech/background 混入禁止の明文の3マーカーを機械強制する。
    """
    confuser = _require_dict(data, field="identity metric space manifest.confuser_control")
    unknown = set(confuser.keys()) - _CONFUSER_CONTROL_REQUIRED_KEYS
    if unknown:
        raise Run9ValidationError(f"confuser_control has unknown key(s): {sorted(unknown)}")
    missing = _CONFUSER_CONTROL_REQUIRED_KEYS - set(confuser.keys())
    if missing:
        raise Run9ValidationError(f"confuser_control missing required key(s): {sorted(missing)}")
    for key in _CONFUSER_CONTROL_STR_KEYS:
        _require_non_empty_str(confuser[key], field=f"confuser_control.{key}")

    role = confuser["role"]
    for marker in (
        _CONFUSER_CONTROL_ROLE_NON_USE_AS_NEGATIVE_REFERENCE_MARKER,
        _CONFUSER_CONTROL_ROLE_CONFUSER_ONLY_USE_MARKER,
    ):
        if marker not in role:
            raise Run9ValidationError(
                f"confuser_control.role must state {marker!r} (Codex bot レビュー PR #318 第13巡 "
                "Fix 33 — PJS's role must be precisely distinguished as 'not used as a negative "
                "reference' vs 'used only as a confuser control', without contradicting Fix 30's "
                f"calibration-gate-scoped non-use declaration), got {role!r}"
            )

    metric = confuser["metric"]
    for marker in (
        _CONFUSER_CONTROL_METRIC_FEATURE_CALL_MARKER,
        _CONFUSER_CONTROL_METRIC_LEVEL_NORMALIZATION_REF_MARKER,
    ):
        if marker not in metric:
            raise Run9ValidationError(
                f"confuser_control.metric must state {marker!r} (Codex bot レビュー PR #318 第13巡 "
                "Fix 33 — the confuser distance must bind by reference to "
                "identity_feature.level_normalization's feature(x) rather than defining a new "
                f"distance formula), got {metric!r}"
            )

    pjs_reference_definition = confuser["pjs_reference_definition"]
    if _PJS_REFERENCE_DEFINITION_OLD_SINGLE_TAKE_REGRESSION_MARKER in pjs_reference_definition:
        raise Run9ValidationError(
            "confuser_control.pjs_reference_definition must not regress to single-take PJS "
            "reference selection (Codex bot レビュー PR #318 第14巡 Fix 34 — single-take selection, "
            "even with a pinned take index/digest, still leaves residual selection discretion and "
            "post-run-only recording of the selected value, which structurally permits post-hoc "
            "cherry-picking of a favorable PJS take after observing post-learning renders; it was "
            "replaced by deterministic full-corpus aggregation), "
            f"got {pjs_reference_definition!r}"
        )
    for marker in (
        _PJS_REFERENCE_DEFINITION_LEXICOGRAPHIC_ENUMERATION_MARKER,
        _PJS_REFERENCE_DEFINITION_ARITHMETIC_MEAN_MARKER,
        _PJS_REFERENCE_DEFINITION_CORPUS_PIN_FIELD_MARKER,
        _PJS_REFERENCE_DEFINITION_VOICED_MASK_EXCLUSION_MARKER,
        _PJS_REFERENCE_DEFINITION_POST_HOC_SELECTION_IMPOSSIBLE_MARKER,
    ):
        if marker not in pjs_reference_definition:
            raise Run9ValidationError(
                f"confuser_control.pjs_reference_definition must state {marker!r} (Codex bot "
                "レビュー PR #318 第14巡 Fix 34 — pjs_reference must be frozen pre-learning as a "
                "deterministic corpus-wide aggregate: lexicographic enumeration of the pinned "
                "expanded corpus, the same extraction procedure per file, mechanical voiced_mask-"
                "based exclusion, element-wise arithmetic mean aggregation, and an explicit "
                "statement that post-hoc PJS-take selection is structurally impossible), "
                f"got {pjs_reference_definition!r}"
            )
    # Fix 35: ③特徴計算クローズが extraction_procedure.sample_rate_normalization
    # を参照していることを要求する（PJS の native 48000Hz を 44100Hz へ変換
    # してから特徴計算する手続きとの相互参照の欠落を拒否する）。
    if _PJS_REFERENCE_DEFINITION_SAMPLE_RATE_NORMALIZATION_REF_MARKER not in pjs_reference_definition:
        raise Run9ValidationError(
            "confuser_control.pjs_reference_definition must cross-reference "
            f"{_PJS_REFERENCE_DEFINITION_SAMPLE_RATE_NORMALIZATION_REF_MARKER!r} (Codex bot レビュー "
            "PR #318 第15巡 Fix 35 — feature computation over the PJS corpus must apply the "
            "extraction_procedure.sample_rate_normalization input-normalization step first, since "
            "PJS's native sample rate (48000 Hz) differs from the pinned metric sample rate "
            f"(44100 Hz)), got {pjs_reference_definition!r}"
        )

    # Fix 37: ②列挙規則が pin 被覆ファイル集合（`_song.wav`）へ限定されて
    # いることを機械強制する。旧「コーパス内の全音声ファイル列挙」規則
    # （speech/background を含む全203 WAV を対象化し、corpus_identity_hash()
    # の被覆外ファイルが pjs_reference・no-leakage evidence を pin を変えず
    # に汚染し得た欠陥）への逆行を拒否する。
    if (
        _PJS_REFERENCE_DEFINITION_OLD_FULL_CORPUS_ENUMERATION_REGRESSION_MARKER
        in pjs_reference_definition
    ):
        raise Run9ValidationError(
            "confuser_control.pjs_reference_definition must not regress to enumerating every "
            "audio file in the bound corpus regardless of pin coverage (Codex bot レビュー PR #318 "
            "第17巡 Fix 37 — the referenced corpus_sha256 (pjs_neutral.json, via "
            "corpus_identity_hash()) covers only .lab + paired _song.wav files; speech/background "
            "WAV outside that coverage can be swapped without changing the pin, contaminating "
            f"pjs_reference and no-leakage evidence), got {pjs_reference_definition!r}"
        )
    for marker in (
        _PJS_REFERENCE_DEFINITION_SONG_WAV_SCOPE_MARKER,
        _PJS_REFERENCE_DEFINITION_CORPUS_IDENTITY_HASH_REF_MARKER,
        _PJS_REFERENCE_DEFINITION_SPEECH_BACKGROUND_EXCLUSION_MARKER,
    ):
        if marker not in pjs_reference_definition:
            raise Run9ValidationError(
                f"confuser_control.pjs_reference_definition must state {marker!r} (Codex bot "
                "レビュー PR #318 第17巡 Fix 37 — the enumeration scope (②) must be limited to the "
                "`_song.wav` files that donor_bank_lab.py's corpus_identity_hash() actually pins "
                "(.lab + paired _song.wav only), with speech/background WAV explicitly excluded "
                "from aggregation because they fall outside pin coverage), "
                f"got {pjs_reference_definition!r}"
            )

    evaluation = confuser["evaluation"]
    if _CONFUSER_CONTROL_EVALUATION_NO_AGGREGATE_SCORE_MARKER not in evaluation:
        raise Run9ValidationError(
            "confuser_control.evaluation must state that no aggregate score / PASS-FAIL threshold "
            f"is produced (expected {_CONFUSER_CONTROL_EVALUATION_NO_AGGREGATE_SCORE_MARKER!r} to "
            f"appear), got {evaluation!r} (Codex bot レビュー PR #318 第13巡 Fix 33 — axis-specific "
            "evidence only, no single Total Score)"
        )
    if _CONFUSER_CONTROL_EVALUATION_CALIBRATION_INDEPENDENCE_MARKER not in evaluation:
        raise Run9ValidationError(
            "confuser_control.evaluation must state its independence from calibration_status(F) "
            f"(expected {_CONFUSER_CONTROL_EVALUATION_CALIBRATION_INDEPENDENCE_MARKER!r} to appear), "
            f"got {evaluation!r} (Codex bot レビュー PR #318 第13巡 Fix 33)"
        )


def validate_identity_metric_space_manifest(data: Mapping[str, Any]) -> None:
    """`inputs/identity_metric_space.json`（`run9-identity-metric-space/1.2`）
    の閉じた形状を検証する（Codex bot レビュー PR #318 第6巡 Fix 19、
    第7巡 Fix 20/Fix 21、第9巡 Fix 23/Fix 24、第11巡 Fix 28、
    第12巡 Fix 29/Fix 30/Fix 31、第13巡 Fix 32/Fix 33、第14巡 Fix 34、
    第15巡 Fix 35、第16巡 Fix 36 で拡張）。

    第16巡 Fix 36（P1）: リサンプル比の native rate からの一般導出。第15巡
    Fix 35 時点の rule は固定 147/160 比を pin していたが、直後の
    applies_to が宣言する「native sr ≠ 44100 Hz のあらゆる入力に適用する
    一般規則」と矛盾していた（例: native 24000 Hz の入力に 147/160 を適用
    すると 22050 Hz へ変換され、WORLD には 44100 Hz として扱われて時間軸・
    周波数軸と identity 距離が壊れる）。rule を g = gcd(44100, native_sr) と
    して scipy.signal.resample_poly(x, up=44100//g, down=native_sr//g) を
    適用する一般導出式へ改訂し、147/160 は native 48000 Hz（PJS の全203
    WAV）に対する導出例として位置づけ直した。他の native rate も同一の
    一般導出式が機械的に up/down を定め、固定比の他 rate への流用は不可と
    明記した。native 44100 Hz の入力は引き続き恒等（変換不要）。validator
    は旧固定比マーカーを一般導出式マーカー（`gcd(44100, native_sr)` /
    `up=44100//g` / `down=native_sr//g`）+ 48kHz 導出例マーカー
    （`resample_poly(x, up=147, down=160)` / `147/160`）へ更新し、固定比
    のみで一般式を欠く旧状態への逆行を拒否する。

    第15巡 Fix 35（P1）: PJS コーパスの metric sample rate への決定論的
    正規化。corpus_inventory_pjs.json によれば PJS の203 WAVは全て48000Hzで
    あり、旧 extraction_procedure.sample_rate は44100Hzを pin するのみで
    再サンプル手続きが存在しなかった（WORLDネイティブ適用ならbin対応周波数
    が食い違い、未凍結の再サンプルなら実装者間で再現不能になる、いずれの
    経路でも confuser_control の d_pjs(r) が壊れる）。着手前調査の結果、
    引用一次ソース donor_bank.py:190-196 analyze_donor_world() は内部で
    固定 sr ロードを行わないことを確認し、新規に
    extraction_procedure.sample_rate_normalization
    （scipy.signal.resample_poly(x, up=147, down=160) — 44100/48000 の
    既約有理比、window は scipy 既定 Kaiser を明示採用）を decisive な
    決定論的変換規則として pin した。native sr が既に44100Hzの入力
    （P0 identity probe founder render 等）は恒等（無変換）。PJS 特例では
    なく「native sr ≠ 44100Hz のあらゆる入力」に適用する一般規則である旨も
    明文化した。confuser_control.pjs_reference_definition の③特徴計算
    クローズにもこの入力正規化ステップへの相互参照を追記した。

    第14巡 Fix 34（P1）: pjs_reference の学習前決定論的凍結。旧
    `confuser_control.pjs_reference_definition` は「事前登録手続きで単一の
    参照レンダー/特徴を選ぶ」としか言っておらず、テイク index・digest・
    生成条件・決定論的集約規則の指定を欠いていた。選定値は post-run の
    `artifact_manifest_sha` 配下にしか記録されないため、評価者が学習後
    レンダーを観察したあとで有利な PJS テイクを選定でき、
    d_pjs(r_learned) の減少有無（no-PJS-leakage evidence）を汚染し得た。
    単一テイク選択を全廃し、①`expanded_corpus_identity_sha256` pin
    フィールドへの入力束縛 ②相対パス辞書順の全件列挙 ③
    extraction_procedure/identity_feature の同一手続き適用 ④voiced_mask
    による機械的（裁量ゼロ）除外 + 除外リストの出生後アーティファクト記録
    ⑤要素ごとの算術平均集約 ⑥計算結果の一方向 provenance 記録（規則・
    入力束縛自体は本 manifest で学習前に凍結済みで評価者に選択自由度は
    存在しない）— の6要素からなる決定論的コーパス集約規則へ置換した。
    `_validate_confuser_control_section()` が pjs_reference_definition の
    旧単一テイク文言への逆行拒否 + 上記5マーカー（辞書順・算術平均・
    corpus pin フィールド参照・voiced_mask 除外・事後選択不可能の明文）を
    機械強制する。

    第13巡 Fix 32（P1）: C1 ゲートの統計的欠陥の是正。C1 のアダプター効果が
    完全にゼロのとき D_C0(F)/D_C1(F) は同一 replay-noise 分布からの独立標本
    であり経験 P95 同士（尾側 vs 尾側）は交換可能なため、旧ゲート
    `P95(D_C1(F)) <= theta_cal(F)` はゼロ効果下でも約1/2の確率で偽って
    不成立となり founder を不当に INVALID 化していた。ゲート条件を
    `P50(D_C1(F)) <= theta_cal(F)`（分布中心 vs 尾側の比較）へ改訂する。

    第13巡 Fix 33（P1）: PJS confuser（C3）評価経路の復元。DESIGN_RUN9
    §14 C3「PJS Confuser」が要求する no-PJS-leakage 検出経路を、第12巡
    Fix 30 の校正ゲート専用 non-use 宣言と区別した新設 `confuser_control`
    節（role/metric/pjs_reference_definition/evaluation）として復元する。
    総合スコア化・PASS/FAIL 化はせず、校正 validity gates とも独立
    （calibration_status(F) を変えない）。

    第12巡 Fix 29（P1）: reference_example を procedure-only の恒久 pin と
    して検証する。status は凍結した唯一の値と厳密一致し、value は常に null
    でなければならない（Fix 21 の非対称ルールを反転 — 実測値の書き戻しは
    metric_space_sha → content_digest() → genome_id の連鎖により循環
    provenance を生むため、書き戻し自体を拒否する）。第12巡 Fix 30（P1）:
    negative_reference_definition が PJS へ言及する場合、必ず「negative
    reference としても使用しない」旨でなければならず、旧矛盾文言（「PJS は
    構造的に排除済み」と述べつつ「negative reference としてのみ利用する」）
    への逆行を拒否する。第12巡 Fix 31（P1）: identity_feature.scope が
    「feature の計算可能域（全 identity 評価対象レンダー）」と「校正・参照に
    使える母集団（neutral な r0 限定）」を区別して明文化していることを
    機械強制する。

    第11巡 Fix 28（P1）: calibration.distance_unit.formula が
    identity_feature.level_normalization の定義する正規化 feature(x) 基準で
    あることを機械強制する。raw な mean_voiced_log_sp ベクトルへの直接
    Euclidean へ逆行すると、level 正規化前のゲイン変化が dynamics のみの
    変化でも再び距離へ漏れ込み、metric_version 0.3 のゲイン不変の主張と
    矛盾する。

    第9巡 Fix 23（P1）: reference_render(F) が C0/C1 母集団に属するか
    （自己比較ゼロ距離混入）が未凍結だった指摘を、`d_c0_population`/
    `d_c1_population`/`reference_render_definition` の文言検証として
    機械強制する。第9巡 Fix 24（P2）: `_validate_nested_str_keys()` を
    必須キー存在チェックからキー集合完全一致（未知キー拒否）へ強化し、
    本関数が呼ぶ全ネスト object（f0_estimation/spectral_envelope/
    voiced_mask/distance_unit/freeze_threshold/decision_rule/
    source_references 等）へ一括適用する。

    旧実装はトップレベルの `schema`/`metric_version` 2ラベルしか検証して
    おらず、digest テスト（正規形 sha256 が pin 値と一致すること）は
    「そこにある形」を祝福するだけだった。repin 時に `extraction_
    procedure` の丸ごと削除・`voiced_mask` の省略・ネスト型変更（例:
    `frame_period_ms` が文字列化される）が起きても、digest テストは
    改変後の内容から再計算した sha が pin 値と一致しさえすれば素通り
    してしまう構造的な穴があった。

    本関数はトップレベル必須キー閉集合・`extraction_procedure` の必須
    ネストキー閉集合と型（harvest/cheaptrick/voiced_mask=f0>0/sample_rate/
    fft/log floor の各キー）・`calibration` 節の必須キー閉集合と型
    （distance_unit/freeze_threshold/validity_gates の3ゲート/
    decision_rule/worked_example の synthetic disclaimer 必須/
    source_references、Fix 20 で pooling 禁止文言 + per-founder 参照の
    欠落も拒否）を検証する。Fix 21（PR #318 第7巡 Fix 21）採用: 旧実装は
    `feature_extractor`/`identity_feature`/`distance`/`reference_example`
    をトップレベルキー集合にのみ含め、内容は既存の `test_phase3_
    identity_metric_space_*` 群（部分的な内容照合テスト）に委ねていた
    ため、これら object 型フィールド全体の null 化やネストキーの欠落・
    追加が repin だけで素通りする穴があった。本関数はこれら4フィールド
    （+ 純メタデータ str の `feasibility_note`）にも `extraction_
    procedure`/`calibration` と同型の閉じた必須ネストキー集合 + 型検証
    を適用する（内容の意味論そのもの — 例えば具体的な文言 — は引き続き
    `test_phase3_*` 群の責務のまま。本 validator は形状 + 最低限の型を
    対象とする）。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(
            f"identity metric space manifest must be an object, got {type(data).__name__}"
        )
    unknown = set(data.keys()) - _IDENTITY_METRIC_SPACE_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"identity metric space manifest has unknown key(s): {sorted(unknown)}")
    missing = _IDENTITY_METRIC_SPACE_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(
            f"identity metric space manifest missing required key(s): {sorted(missing)}"
        )

    schema = data["schema"]
    if schema != SCHEMA_IDENTITY_METRIC_SPACE:
        raise Run9ValidationError(
            f"identity metric space manifest schema must be exactly {SCHEMA_IDENTITY_METRIC_SPACE!r}, "
            f"got {schema!r}"
        )
    _require_non_empty_str(data["metric_version"], field="identity metric space manifest.metric_version")
    _require_non_empty_str(
        data["canonicalization_method"], field="identity metric space manifest.canonicalization_method"
    )

    _validate_feature_extractor(data["feature_extractor"])
    _validate_extraction_procedure(data["extraction_procedure"])
    _validate_identity_feature(data["identity_feature"])
    _validate_distance_section(data["distance"])
    _validate_calibration_section(data["calibration"])
    _validate_reference_example(data["reference_example"])
    _validate_confuser_control_section(data["confuser_control"])
    _require_non_empty_str(
        data["feasibility_note"], field="identity metric space manifest.feasibility_note"
    )


# PoR §12 + User 外部レビュー PR #317 P1-2 修正指示4の逐語項目を機械可読
# キー名へ写した最低要件（practice split manifest）。
PRACTICE_MANIFEST_REQUIRED_KEYS: Tuple[str, ...] = (
    "pjs_source_archive_sha256",  # PJS source archive pin
    "expanded_corpus_identity_sha256",  # expanded corpus identity pin
    "training_split_sha256",  # training split hash
    "validation_split_sha256",  # validation split hash
    "sealed_holdout_sha256",  # sealed holdout root/hash
    "row_order_sha256",  # row order hash
    "sample_inventory",  # sample inventory
    "rights_source_class",  # rights/source class
    "is_raw_audio",  # raw音声であること（bool True 必須）
    "excludes_correct_technique_parameters",  # 正解 Technique parameter を含まないこと（bool True 必須）
    "identical_bytes_and_order_across_founders",  # 二体へ同一bytes・同一順序で提示すること（bool True 必須）
)

# PoR §12 + 修正指示5の逐語項目を機械可読キー名へ写した最低要件
# （education technique lesson manifest）。
EDUCATION_MANIFEST_REQUIRED_KEYS: Tuple[str, ...] = (
    "training_technique_lesson_sha256",  # training音声から生成したTechnique lesson
    "validation_technique_lesson_sha256",  # validation lesson
    "sealed_holdout_technique_release_policy",  # sealed holdout Techniqueは学習後にのみ生成/開封
    "excludes_identity_and_trait_donor_info",  # PJS Identity/speaker embedding/voice-trait donor情報の排除証拠（bool True 必須）
    "identical_lesson_bytes_across_founders",  # 二体へ同一lesson bytesを提示すること（bool True 必須）
)

_MANIFEST_REQUIRED_TRUE_KEYS: FrozenSet[str] = frozenset({
    "is_raw_audio",
    "excludes_correct_technique_parameters",
    "identical_bytes_and_order_across_founders",
    "excludes_identity_and_trait_donor_info",
    "identical_lesson_bytes_across_founders",
})

# rev 0.3（User 外部レビュー PR #317 P1-2 → Codex bot レビュー第4巡 Fix A
# 採用）: manifest 必須欄のうち `_sha256` 終端キーは値整形式（64hex）まで
# 強制する。第1〜3巡実装は presence-only（キーが存在するかどうか）しか
# 検証しておらず、「builder が不完全 artifact を出しても byte-hash だけで
# PINNED contract が作れ、使用可能な素材ゼロで READY に至る」偽成功経路が
# validator 層で閉じていなかった。
_PRACTICE_MANIFEST_SHA256_KEYS: FrozenSet[str] = frozenset({
    "pjs_source_archive_sha256", "expanded_corpus_identity_sha256",
    "training_split_sha256", "validation_split_sha256",
    "sealed_holdout_sha256", "row_order_sha256",
})
_EDUCATION_MANIFEST_SHA256_KEYS: FrozenSet[str] = frozenset({
    "training_technique_lesson_sha256", "validation_technique_lesson_sha256",
})

# `sealed_holdout_technique_release_policy` の閉じた語彙（Codex bot レビュー
# 第4巡 Fix A 採用）: PoR §12「sealed holdout Technique は学習終了後に
# 開封」— 正当な release policy は「training 完了後にのみ開封する」の
# 1値のみであり、他の値（例: 学習中の早期開封を許すもの）は PoR の holdout
# 規律に反するため存在させない。将来別の正当な policy が追加される場合は
# 本タプルを拡張する（新しい design_revision を要する設計判断）。
EDUCATION_SEALED_HOLDOUT_RELEASE_POLICIES: Tuple[str] = ("RELEASE_AFTER_TRAINING_COMPLETE",)


def _require_nonempty_str(value: Any, *, manifest_kind: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Run9ValidationError(
            f"{manifest_kind}.{field} must be a non-empty string, got {value!r}"
        )
    return value


def _require_manifest_sha256_hex(value: Any, *, manifest_kind: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
        raise Run9ValidationError(
            f"{manifest_kind}.{field} must be exactly 64 lowercase hex characters (sha256 format), "
            f"got {value!r}"
        )
    return value


def _require_nonempty_str_list(value: Any, *, manifest_kind: str, field: str) -> List[str]:
    if not isinstance(value, list) or not value:
        raise Run9ValidationError(
            f"{manifest_kind}.{field} must be a non-empty list, got {value!r}"
        )
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise Run9ValidationError(
                f"{manifest_kind}.{field}[{i}] must be a non-empty string, got {item!r}"
            )
    return value


def _require_manifest_hash_fields(
    data: Mapping[str, Any], *, hash_keys: FrozenSet[str], manifest_kind: str
) -> None:
    for key in hash_keys:
        _require_manifest_sha256_hex(data[key], manifest_kind=manifest_kind, field=key)


def _require_no_duplicate_list_items(value: List[Any], *, manifest_kind: str, field: str) -> None:
    """`value` 内に重複要素が無いことを検証する（Codex bot レビュー第4巡
    Fix B 採用の row_ids 版を、第5巡 Fix B 採用で `sample_inventory` へも
    適用できる共有ヘルパへ抽出）。`set(value)` への変換は重複を黙って
    握り潰すため、`len(value) != len(set(value))` を先行させて重複自体を
    検出してから、実際に重複した値を報告する。
    """
    if len(value) != len(set(value)):
        seen: set = set()
        duplicates = sorted({v for v in value if v in seen or seen.add(v)})
        raise Run9ValidationError(
            f"{manifest_kind}.{field} contains duplicate value(s): {duplicates} — "
            "duplicate entries would be silently collapsed by set-based dedup while an "
            "order-preserving harness would still consume the entry multiple times"
        )


def _require_disjoint_row_id_sets(
    *, training: Any, validation: Any, sealed_holdout: Any, manifest_kind: str
) -> None:
    """training/validation/sealed_holdout の row id 集合が互いに素で
    あることを検証する（User 外部レビュー PR #317 P1-2 必須テスト
    「holdout が training 集合へ混入した manifest を拒否」の実装）。
    3集合いずれも**非空** list であることを先に強制する（Codex bot レビュー
    第4巡 Fix A 採用: 3 split 全空の manifest — 使用可能な素材ゼロ — が
    disjoint 検査自体は素通しで通過してしまっていた）。

    disjoint 検査の前に、各 split 内で重複 row ID が無いことを強制する
    （Codex bot レビュー第4巡 Fix B 採用、第5巡 Fix B で共有ヘルパへ
    抽出）: `set(training)` への変換は重複を黙って握り潰すため、
    `["r1","r1","r2"]` のような list をそのまま消費するハーネスが同一
    row を複数回学習/評価し、gain 推定を汚す経路が閉じていなかった。
    """
    splits: Dict[str, List[Any]] = {"training": training, "validation": validation, "sealed_holdout": sealed_holdout}
    for name, value in splits.items():
        _require_nonempty_str_list(value, manifest_kind=manifest_kind, field=f"row_ids.{name}")
    for name, value in splits.items():
        _require_no_duplicate_list_items(value, manifest_kind=manifest_kind, field=f"row_ids.{name}")
    training_set, validation_set, holdout_set = set(training), set(validation), set(sealed_holdout)
    overlap_th = training_set & holdout_set
    if overlap_th:
        raise Run9ValidationError(
            f"{manifest_kind}.row_ids: sealed_holdout overlaps training — leaked row id(s): "
            f"{sorted(overlap_th)}"
        )
    overlap_vh = validation_set & holdout_set
    if overlap_vh:
        raise Run9ValidationError(
            f"{manifest_kind}.row_ids: sealed_holdout overlaps validation — leaked row id(s): "
            f"{sorted(overlap_vh)}"
        )
    overlap_tv = training_set & validation_set
    if overlap_tv:
        raise Run9ValidationError(
            f"{manifest_kind}.row_ids: training overlaps validation — leaked row id(s): "
            f"{sorted(overlap_tv)}"
        )


_FOUNDER_ID_KEY_NAME = "founder_id"


def _reject_per_founder_split_structure(data: Mapping[str, Any], *, manifest_kind: str) -> None:
    """manifest が Founder ごとに異なる split/lesson を与える構造を拒否
    する（User 外部レビュー PR #317 P1-2 必須テスト「Founder ごとに異なる
    practice split を与える構造を拒否」の実装。Codex bot レビュー第5巡
    Fix A 採用で検出範囲を拡張）。「manifest は二体共通・単一系列であり
    founder 分岐構造を持たない」という原則の機械的裏付け — PoR §12/修正
    指示は「二体へ同一 bytes・同一順序で提示する」ことを要求しており、
    manifest 自体が Founder 分岐を持つ時点でこの要求と矛盾する。

    2つの独立した規則に分けて実装する（第5巡 Fix A 採用: 従来は
    `R9F-0[12]` 形式のキーのみを走査しており、`{"founder_id": "R9F-01",
    ...}` のような**値フィールド**での founder 分岐が素通りしていた）:

    1. **`founder_id` キー自体の禁止**（任意の深さ）: キー名が正確に
       `"founder_id"` であれば、値の中身に関わらず拒否する。
       "founder_identity_note" のような接頭辞一致の誤爆を避けるため
       完全一致のみを対象とする。
    2. **`R9F-01`/`R9F-02` の完全一致文字列がキーまたは値として現れた
       場合の禁止**（任意の深さ、dict のキー・値・list の要素いずれも
       走査）: sample id 等の正当な文字列に founder ID が部分文字列と
       して含まれる場合の誤爆を避けるため、`_FOUNDER_ID_RE`
       （`^R9F-0[12]$`）による**完全一致のみ**を対象とし、部分一致
       （例: `"clip-R9F-01-take3"`）は対象外とする。
    """

    def _is_founder_id_value(value: Any) -> bool:
        return isinstance(value, str) and bool(_FOUNDER_ID_RE.match(value))

    def _scan(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_str = str(key)
                if key_str == _FOUNDER_ID_KEY_NAME:
                    raise Run9ValidationError(
                        f"{manifest_kind} must not contain a {_FOUNDER_ID_KEY_NAME!r} key "
                        f"(found value {value!r}) — manifest は二体共通・単一系列であり founder "
                        "分岐構造を持たない（PoR §12: 二体へ同一 bytes・同一順序で提示する）"
                    )
                if _FOUNDER_ID_RE.match(key_str):
                    raise Run9ValidationError(
                        f"{manifest_kind} must not branch by founder_id (found key {key!r}) — "
                        "manifest は二体共通・単一系列であり founder 分岐構造を持たない（PoR §12: "
                        "二体へ同一 bytes・同一順序で提示する）"
                    )
                if _is_founder_id_value(value):
                    raise Run9ValidationError(
                        f"{manifest_kind} must not contain a founder ID as a value (key={key!r}, "
                        f"value={value!r}) — manifest は二体共通・単一系列であり founder 分岐構造を"
                        "持たない（PoR §12: 二体へ同一 bytes・同一順序で提示する）"
                    )
                _scan(value)
        elif isinstance(node, list):
            for item in node:
                if _is_founder_id_value(item):
                    raise Run9ValidationError(
                        f"{manifest_kind} must not contain a founder ID as a list element "
                        f"(found {item!r}) — manifest は二体共通・単一系列であり founder 分岐構造を"
                        "持たない（PoR §12: 二体へ同一 bytes・同一順序で提示する）"
                    )
                _scan(item)

    _scan(data)


def validate_practice_split_manifest(data: Mapping[str, Any]) -> None:
    """PRACTICE_FROM_AUDIO 用 train/validation/sealed-holdout split
    manifest の最低要件を検証する。`schema` が
    `SCHEMA_PRACTICE_AUDIO_SPLIT_MANIFEST` と厳密一致しない入力（例:
    education manifest を取り違えて渡した場合）は schema 不一致で拒否する
    （User 外部レビュー PR #317 P1-2 必須テスト「manifest hash を入れ替え
    た場合に拒否」の schema 側の実装 — 実ファイルのバイト取り違えは
    RUN9_CONTRACT.yaml 側の pin 値照合が担うため、本関数はパース済み dict
    の内容が practice manifest として自己整合的であることのみを見る）。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(f"practice split manifest must be an object, got {type(data).__name__}")
    schema = data.get("schema")
    if schema != SCHEMA_PRACTICE_AUDIO_SPLIT_MANIFEST:
        raise Run9ValidationError(
            f"practice split manifest schema must be exactly {SCHEMA_PRACTICE_AUDIO_SPLIT_MANIFEST!r}, "
            f"got {schema!r} (a manifest declaring a different or missing schema — e.g. an education "
            "lesson manifest passed by mistake — must not be treated as the practice split manifest)"
        )
    missing = [k for k in PRACTICE_MANIFEST_REQUIRED_KEYS if k not in data]
    if missing:
        raise Run9ValidationError(f"practice split manifest missing required key(s): {sorted(missing)}")
    for key in _MANIFEST_REQUIRED_TRUE_KEYS & set(PRACTICE_MANIFEST_REQUIRED_KEYS):
        if data[key] is not True:
            raise Run9ValidationError(
                f"practice split manifest.{key} must be exactly True, got {data[key]!r}"
            )
    # Codex bot レビュー第4巡 Fix A 採用: hash 系欄の値整形式（64hex）・
    # sample_inventory / rights_source_class の非空検査を presence-only
    # 検査に追加する。
    _require_manifest_hash_fields(
        data, hash_keys=_PRACTICE_MANIFEST_SHA256_KEYS, manifest_kind="practice split manifest"
    )
    _require_nonempty_str_list(
        data["sample_inventory"], manifest_kind="practice split manifest", field="sample_inventory"
    )
    # Codex bot レビュー第5巡 Fix B 採用: row_ids と同じ重複拒否を
    # sample_inventory にも適用する（重複 sample id は、同一素材が複数回
    # 数えられた見かけ上のカバレッジ水増しを招く）。
    _require_no_duplicate_list_items(
        data["sample_inventory"], manifest_kind="practice split manifest", field="sample_inventory"
    )
    _require_nonempty_str(
        data["rights_source_class"], manifest_kind="practice split manifest", field="rights_source_class"
    )
    # Founder 分岐構造の検査を disjoint 検査より先に行う（Founder ごとに
    # 異なる split を与える構造そのものが最上位の欠陥であり、その場合
    # row_ids.training 等が期待形でないのは当然の帰結に過ぎない — エラー
    # メッセージが「Founder 分岐」という根本原因を正確に指すようにする）。
    _reject_per_founder_split_structure(data, manifest_kind="practice split manifest")
    row_ids = data.get("row_ids")
    if not isinstance(row_ids, dict):
        raise Run9ValidationError(
            f"practice split manifest.row_ids must be an object, got {type(row_ids).__name__}"
        )
    _require_disjoint_row_id_sets(
        training=row_ids.get("training"),
        validation=row_ids.get("validation"),
        sealed_holdout=row_ids.get("sealed_holdout"),
        manifest_kind="practice split manifest",
    )


def validate_education_lesson_manifest(data: Mapping[str, Any]) -> None:
    """TRANSFER_TECHNIQUE 用 Technique lesson manifest の最低要件を検証
    する。`validate_practice_split_manifest()` と対の構造 —
    `schema` が `SCHEMA_EDUCATION_TECHNIQUE_LESSON_MANIFEST` と厳密一致
    しない入力は拒否する。"""
    if not isinstance(data, dict):
        raise Run9ValidationError(f"education lesson manifest must be an object, got {type(data).__name__}")
    schema = data.get("schema")
    if schema != SCHEMA_EDUCATION_TECHNIQUE_LESSON_MANIFEST:
        raise Run9ValidationError(
            f"education lesson manifest schema must be exactly "
            f"{SCHEMA_EDUCATION_TECHNIQUE_LESSON_MANIFEST!r}, got {schema!r} (a manifest declaring a "
            "different or missing schema — e.g. a practice split manifest passed by mistake — must "
            "not be treated as the education lesson manifest)"
        )
    missing = [k for k in EDUCATION_MANIFEST_REQUIRED_KEYS if k not in data]
    if missing:
        raise Run9ValidationError(f"education lesson manifest missing required key(s): {sorted(missing)}")
    for key in _MANIFEST_REQUIRED_TRUE_KEYS & set(EDUCATION_MANIFEST_REQUIRED_KEYS):
        if data[key] is not True:
            raise Run9ValidationError(
                f"education lesson manifest.{key} must be exactly True, got {data[key]!r}"
            )
    # Codex bot レビュー第4巡 Fix A 採用: lesson hash 群の値整形式（64hex）
    # + sealed_holdout_technique_release_policy の閉じた語彙検証を
    # presence-only 検査に追加する。
    _require_manifest_hash_fields(
        data, hash_keys=_EDUCATION_MANIFEST_SHA256_KEYS, manifest_kind="education lesson manifest"
    )
    release_policy = data["sealed_holdout_technique_release_policy"]
    if release_policy not in EDUCATION_SEALED_HOLDOUT_RELEASE_POLICIES:
        raise Run9ValidationError(
            "education lesson manifest.sealed_holdout_technique_release_policy must be one of "
            f"{list(EDUCATION_SEALED_HOLDOUT_RELEASE_POLICIES)}, got {release_policy!r}"
        )
    _reject_per_founder_split_structure(data, manifest_kind="education lesson manifest")


# ---------------------------------------------------------------------------
# probe manifest（RUN9-PROBE-1, DESIGN_RUN9 §15 Probe Set の実体 manifest）:
# P0-P5 の score cells + render 契約 + revision_bridge（§15 probe 語彙 ↔
# identity_metric_space 語彙の橋渡し）を単一ファイルへ凍結する。「どう
# 測るか」は本 manifest の対象外のまま——identity 軸は
# `inputs/identity_metric_space.json` が正本、development/generalization
# 軸の測定仕様は `measurement_spec_sha`（別欄、PENDING のまま）が別途
# 凍結する（`measurement_boundary` 節が明文化）。
# ---------------------------------------------------------------------------

SCHEMA_PROBE_MANIFEST = "run9-probe-manifest/1.0"

# 規約パス（`PRACTICE_MANIFEST_PATH` 等と同じ命名規約 — schema から機械的
# に導出せず、リポジトリ内の固定配置として凍結する）。`evaluation/` は
# 本 manifest が初出のディレクトリ。
PROBE_MANIFEST_PATH = _THIS_DIR / "evaluation" / "probe_manifest.json"

# DESIGN_RUN9 §15 が定義する Probe Set の閉語彙（記載順）。
PROBE_IDS: Tuple[str, str, str, str, str, str] = ("P0", "P1", "P2", "P3", "P4", "P5")

# §15 の名称を逐語で固定する（probe.title はこの値と厳密一致でなければ
# ならない）。design_source はどの probe も同一の §15 参照。
PROBE_TITLES: Mapping[str, str] = types.MappingProxyType({
    "P0": "Neutral Identity Probe",
    "P1": "Pitch / Duration Probe",
    "P2": "Energy / Attack Probe",
    "P3": "Phrase-End Probe",
    "P4": "Held-out Song",
    "P5": "Held-out Register / Phrase",
})

PROBE_DESIGN_SOURCE = "DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md §15"

_PROBE_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "schema", "probes", "render_contract", "revision_bridge",
    "measurement_boundary", "prohibitions", "note",
})

# probe object の基本閉集合（全 probe 共通）。P1-P3 は起草要求（項目4）が
# 明記する `factor_levels`（水準表）、P4 は `heldout_independence`（独立性
# 宣言）を、汎用の role/cells とは別の追加必須キーとして要求する。
_PROBE_BASE_KEYS: FrozenSet[str] = frozenset({"probe_id", "title", "design_source", "role", "cells"})
_PROBE_REQUIRED_EXTRA_KEYS: Mapping[str, FrozenSet[str]] = types.MappingProxyType({
    "P0": frozenset(),
    "P1": frozenset({"factor_levels"}),
    "P2": frozenset({"factor_levels"}),
    "P3": frozenset({"factor_levels"}),
    "P4": frozenset({"heldout_independence"}),
    "P5": frozenset(),
})

_CELL_KEYS_BASE: FrozenSet[str] = frozenset({"cell_id", "tempo_bpm", "notes"})
# P0 cell のみ `source`（score.py への転記元メタデータ）を追加で要求する
# （起草要求 P0: 「cell の source メタデータに転記元…を記録」）。
_CELL_SOURCE_KEY = "source"
_CELL_SOURCE_KEYS: FrozenSet[str] = frozenset(
    {"transcribed_from", "transcribed_from_sha256", "transcription_scope", "verbatim"}
)

# PR #322 第1巡指摘 Fix 2（P2, 採用）: P1-P3 は `factor_levels` の形状と
# cell 対応の双方向検証を要求する。cell 側にどの水準の組かを機械可読に
# 持たせる `levels` メタデータ（cell_id 文字列のパースに依存しない）。
_CELL_LEVELS_KEY = "levels"
# `factor_levels` が形状検証・cell 対応検証の対象とする probe（水準表を
# 持つ probe と同一集合 — `_PROBE_REQUIRED_EXTRA_KEYS` の "factor_levels"
# 要求 probe と揃える）。
_FACTOR_LEVEL_PROBE_IDS: FrozenSet[str] = frozenset({"P1", "P2", "P3"})
# `factor_levels` 内の「軸」節を保持する必須キー。他の記述的メタデータ
# キー（`source_precedent`/`medial_filler_kana` 等）は axes とは別に
# 自由記述のまま許容する（本 Fix の対象は axes の形状・cell 対応のみ）。
_FACTOR_LEVELS_AXES_KEY = "axes"

# ---------------------------------------------------------------------------
# PR #322 第4巡指摘 Fix 9（P2, 採用）: 旧 Fix 2/3 は「各水準がどこかの
# cell で使われている」ことしか要求しておらず、cell を削除しても他 cell
# が同じ水準を使っていれば通過してしまっていた（例: P1-REG-LOW-DUR-SHORT
# を削除しても low/short は他 cell に残る）——凍結された factorial から
# 1条件が黙って失われる欠陥。probe 別の期待 cell_id 集合（全24個、閉じた
# 集合・過不足いずれも拒否）を凍結し、加えて P1（register×duration）/
# P3（release_duration×ending_voicing）の full factorial 直積被覆
# （宣言水準の全組合せが「同一 cell」の levels として実在すること）を
# 別途検証する。amendment で cell を増減する場合は、本ファイルの凍結表
# （`_PROBE_EXPECTED_CELL_IDS`/`_PROBE_FACTORIAL_AXES`）の更新が同時に
# 必要——意図的な二重 pin（manifest 側の変更だけでは通らない摩擦）。
# ---------------------------------------------------------------------------
_PROBE_EXPECTED_CELL_IDS: Mapping[str, FrozenSet[str]] = types.MappingProxyType({
    "P0": frozenset({"P0-NEUTRAL-SAKURA-FRAGMENT"}),
    "P1": frozenset({
        "P1-REG-LOW-DUR-SHORT", "P1-REG-LOW-DUR-LONG",
        "P1-REG-MID-DUR-SHORT", "P1-REG-MID-DUR-LONG",
        "P1-REG-HIGH-DUR-SHORT", "P1-REG-HIGH-DUR-LONG",
        "P1-TRANS-LOW-TO-HIGH", "P1-TRANS-HIGH-TO-LOW",
    }),
    "P2": frozenset({
        "P2-ONSET-FRICATIVE-S", "P2-ONSET-STOP-K", "P2-ONSET-STOP-G", "P2-ONSET-NASAL-N",
        "P2-ONSET-SEMIVOWEL-Y", "P2-ONSET-SEMIVOWEL-W", "P2-ONSET-LIQUID-R", "P2-ONSET-VOWEL-ONLY",
        "P2-PHRASE-BUILD-WEAK-TO-STRONG",
    }),
    "P3": frozenset({
        "P3-RELEASE-SHORT-VOICED", "P3-RELEASE-LONG-VOICED",
        "P3-RELEASE-SHORT-UNVOICED", "P3-RELEASE-LONG-UNVOICED",
    }),
    "P4": frozenset({"P4-HELDOUT-ORIGINAL-FRAGMENT"}),
    "P5": frozenset({"P5-HELDOUT-REGISTER-PHRASE"}),
})
# full factorial 直積被覆を要求する probe -> (axis_a, axis_b)。両軸を
# 同時に参照する cell が、宣言水準の全組合せ分だけ存在することを要求する
# （P2 の onset_consonant_class は単一軸で直積構造を持たないため対象外）。
_PROBE_FACTORIAL_AXES: Mapping[str, Tuple[str, str]] = types.MappingProxyType({
    "P1": ("register", "duration"),
    "P3": ("release_duration", "ending_voicing"),
})


def _validate_probe_expected_cell_ids(
    *, expected_probe_id: str, cells: List[Dict[str, Any]], field: str
) -> None:
    expected = _PROBE_EXPECTED_CELL_IDS[expected_probe_id]
    actual = {cell.get("cell_id") for cell in cells}
    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        detail = []
        if missing:
            detail.append(f"missing {sorted(missing)}")
        if extra:
            detail.append(f"unexpected {sorted(extra)}")
        raise Run9ValidationError(
            f"{field}.cells cell_id set does not match the frozen expected set for "
            f"{expected_probe_id} ({'; '.join(detail)}) — amendment requires updating both the "
            "manifest and this validator's frozen expected-cell-id table (Fix 9: intentional double "
            "pin)"
        )


def _validate_probe_factorial_coverage(
    *, expected_probe_id: str, factor_levels: Mapping[str, Any], cells: List[Dict[str, Any]],
    field: str,
) -> None:
    axes_pair = _PROBE_FACTORIAL_AXES.get(expected_probe_id)
    if axes_pair is None:
        return
    axis_a, axis_b = axes_pair
    axes = factor_levels[_FACTOR_LEVELS_AXES_KEY]
    expected_pairs = {
        (level_a, level_b) for level_a in axes[axis_a] for level_b in axes[axis_b]
    }
    covered_pairs = set()
    for cell in cells:
        levels = cell.get(_CELL_LEVELS_KEY, {})
        if isinstance(levels, dict) and axis_a in levels and axis_b in levels:
            covered_pairs.add((levels[axis_a], levels[axis_b]))
    missing_pairs = expected_pairs - covered_pairs
    if missing_pairs:
        raise Run9ValidationError(
            f"{field}: {expected_probe_id} full factorial {axis_a}×{axis_b} is missing combination(s) "
            f"{sorted(missing_pairs)} — every declared level combination must be realized by a single "
            "cell that references both axes simultaneously (a frozen factorial silently losing a "
            "condition is fail-closed, Fix 9)"
        )

# ---------------------------------------------------------------------------
# PR #322 第2巡指摘 Fix 3（P2, 採用）: 軸別の意味照合。Fix 2 はラベル
# （axis_name/level_name）の実在照合のみで、宣言された具体値と cell の実
# note フィールドとの一致は見ていなかった（例: P1-REG-LOW-DUR-SHORT の
# MIDI を 57→65 に変えても `levels: {register: low}` のまま通過してい
# た）。以下、各軸の「宣言 ↔ 実 note」照合をここで凍結する。数値軸
# （register/duration/release_duration）は cell の**phrase-final note**
# （`is_phrase_final: true`。P1/P3 の対象 cell は必ずちょうど1つ持つ —
# 単一 note の register/duration cell では唯一の note、P2/P3 の複数 note
# cell では終端/target note）の該当フィールドと厳密等値照合する。
# transition_direction は先頭 note → 終端 note の pitch_midi 系列を
# `"{start}->{end}"` として文字列照合する。onset_consonant_class /
# ending_voicing は kana → クラスの対応表（validator 内で独立に凍結 —
# manifest 側の記述テキストを自己参照しない）を phrase-final note の kana
# へ適用して照合する。phrase_dynamics は velocity/dynamics 欄が note
# schema に存在しないため、構造（非減少 pitch 系列 + phrase-final note が
# 末尾）の実在を照合する。
# ---------------------------------------------------------------------------

# onset_consonant_class の各水準に属する kana → level_name の対応表
# （P2 factor_levels.axes.onset_consonant_class の記述テキストと同じ
# メンバー構成だが、manifest 側の自由記述文字列をパースするのではなく
# validator 内の独立した凍結表として保持する — 記述文字列と note の両方
# が同時にずれて「整合しているように見える」誤りを検出できない循環照合を
# 避けるため）。
_ONSET_CONSONANT_CLASS_KANA_TABLE: Mapping[str, str] = types.MappingProxyType({
    "さ": "fricative_s", "そ": "fricative_s", "す": "fricative_s",
    "く": "stop_k", "か": "stop_k",
    "ぎ": "stop_g_voiced",
    "の": "nasal_n",
    "や": "semivowel_y", "よ": "semivowel_y",
    "わ": "semivowel_w",
    "ら": "liquid_r", "り": "liquid_r",
    "い": "vowel_only",
})

# ending_voicing の kana → 有声性区分の対応表。標準的な日本語の無声化
# 規則（無声子音 + 狭母音 /i//u/ は無声化しやすい）に基づく（P3
# factor_levels.axes.ending_voicing の記述と同一の理解に基づく独立表）。
# vocabulary に登場する16 kana 全件を分類する（登場していない水準・軸の
# ためだが、将来 cell が増えた際にも kana table の再定義を要さない）。
_ENDING_VOICING_KANA_TABLE: Mapping[str, str] = types.MappingProxyType({
    "く": "unvoiced", "す": "unvoiced",  # 無声子音 + /u/ = 無声化しやすい
    "さ": "voiced", "そ": "voiced", "か": "voiced", "ぎ": "voiced",
    "の": "voiced", "や": "voiced", "よ": "voiced", "わ": "voiced",
    "ら": "voiced", "り": "voiced", "い": "voiced", "は": "voiced",
    "み": "voiced", "た": "voiced",
})

# 数値照合軸: axis_name -> 照合対象の note フィールド名。
_AXIS_NUMERIC_FIELD_CHECKS: Mapping[str, str] = types.MappingProxyType({
    "register": "pitch_midi",
    "duration": "duration_beats",
    "release_duration": "duration_beats",
})
# kana クラス照合軸: axis_name -> kana 対応表。
_AXIS_KANA_CLASS_CHECKS: Mapping[str, Mapping[str, str]] = types.MappingProxyType({
    "onset_consonant_class": _ONSET_CONSONANT_CLASS_KANA_TABLE,
    "ending_voicing": _ENDING_VOICING_KANA_TABLE,
})

# PR #322 第3巡指摘 Fix 7（P2, 採用）: 第2巡 Fix 3 の onset checker は
# phrase-final の検定 note しか見ておらず、P2 onset cell の前置 filler
# note（か・1拍相当）を cell ごとに別 kana/pitch/duration へ変えても
# 通過してしまっていた（前コンテキスト交絡で onset class 比較が壊れる）。
# P2 の `factor_levels` へ凍結 filler タプル（`medial_filler_kana`/
# `medial_filler_beats`/`medial_filler_pitch_midi`）を宣言し、全 onset
# cell（`onset_consonant_class` 軸を持つ cell）の前置 note 列がこの
# タプルと完全一致することを機械強制する。
_P2_FILLER_TUPLE_KEYS: FrozenSet[str] = frozenset(
    {"medial_filler_kana", "medial_filler_beats", "medial_filler_pitch_midi"}
)
_P2_ONSET_AXIS_NAME = "onset_consonant_class"

_NOTE_KEYS: FrozenSet[str] = frozenset(
    {"kana", "pitch_midi", "duration_beats", "phrase_index", "is_phrase_final"}
)

# P0 の score 転記元（read-only 参照。凍結・改変禁止 — RUN9-PROBE-1
# Design Memo 冒頭）。`voice_genesis/singer/score.py`。
SCORE_PY_REFERENCE_PATH = _THIS_DIR.parent.parent / "singer" / "score.py"

# ---------------------------------------------------------------------------
# PR #322 第3巡指摘 Fix 6（P2, 採用）: renderer の mora 文法（read-only
# 参照。凍結・改変禁止）を唯一の正本として note.kana を検証する。
# validator 内での語彙表複製（kana -> mora 対応表を validator 側で再定義
# すること）は renderer 側の対応範囲拡張・変更に追随できず乖離の温床に
# なるため不採用——`voice_genesis/singer/phoneme_jp.py` を read-only で
# ロードし、`kana_to_morae()` をそのまま呼び出す。renderer 文法は repo
# バイトの一部であり、run 時に `repository_commit_sha` が repo バイト
# 全体を pin することで凍結される（Fix 1 の harness runtime seed 凍結
# 根拠と同じ論理）。
# ---------------------------------------------------------------------------
PHONEME_JP_REFERENCE_PATH = _THIS_DIR.parent.parent / "singer" / "phoneme_jp.py"


def _load_phoneme_jp_module(*, path: Optional[Path] = None) -> Any:
    """`voice_genesis/singer/phoneme_jp.py`（凍結・改変禁止の read-only
    参照）を read-only でロードする。`path` 省略時（`None`）はモジュール
    定数 `PHONEME_JP_REFERENCE_PATH` を呼び出しのたびに参照する
    （`_validate_probe_cell_source()`/`_load_identity_metric_space_
    document()` と同じ late-binding 回避パターン——テストが `run9_schema.
    PHONEME_JP_REFERENCE_PATH` を monkeypatch しても本関数の既定値へ
    反映されない def 時束縛の罠を避ける）。ファイル不在・import 失敗・
    `kana_to_morae` 未定義はいずれも fail-closed（Fix 4「照合できない =
    検証失敗」と同じ原則。実 phoneme_jp.py の rename/削除は一切行わない
    ——テスト用の依存性注入点は `path` 引数のみ）。
    """
    if path is None:
        path = PHONEME_JP_REFERENCE_PATH
    if not path.is_file():
        raise Run9ValidationError(
            f"note kana の mora 文法検証には {path} の実在が必須だが見つからない（凍結・改変禁止の "
            "read-only 参照 — 本 validator は repo checkout 内での実行を前提とする。照合できない = "
            "検証失敗、PR #322 第2巡 Fix 4 と同じ fail-closed 原則）"
        )
    module_name = "_run9_probe_manifest_phoneme_jp_readonly"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise Run9ValidationError(f"{path} の import spec を構築できない")
        module = importlib.util.module_from_spec(spec)
        # `phoneme_jp.py` は `@dataclass` を使用しており、dataclasses 内部
        # が `sys.modules[cls.__module__]` を参照する（型ヒント解決の
        # ため）。`module_from_spec()` だけでは `sys.modules` へ登録され
        # ないため、`exec_module()` 前に明示登録する必要がある——登録
        # しないと `AttributeError: 'NoneType' object has no attribute
        # '__dict__'` でロード自体が失敗する。
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Run9ValidationError:
        raise
    except Exception as exc:  # pragma: no cover - defensive fail-closed
        raise Run9ValidationError(f"{path} のロードに失敗した: {exc}") from exc
    if not hasattr(module, "kana_to_morae"):
        raise Run9ValidationError(f"{path} に kana_to_morae() が定義されていない")
    return module


def _require_single_mora_kana(kana: str, *, phoneme_jp_module: Any, field: str) -> None:
    """PR #322 第3巡指摘 Fix 6（P2, 採用）の実装: `kana` が renderer の
    `phoneme_jp.kana_to_morae()` へ通したとき、ちょうど1モーラを構成する
    ことを検証する（1 note = 1 Mora。メリスマ非対応・未対応文字は
    fail-closed で拒否 — score.py docstring「1 モーラ = 1 ノート」と
    同一の制約を機械強制する）。"""
    try:
        morae = phoneme_jp_module.kana_to_morae(kana)
    except Exception as exc:
        raise Run9ValidationError(
            f"{field} = {kana!r} は phoneme_jp.kana_to_morae() が受理しない（renderer 文法の対応外 "
            f"文字を含む可能性がある）: {exc}"
        ) from exc
    if len(morae) != 1:
        raise Run9ValidationError(
            f"{field} = {kana!r} は phoneme_jp.kana_to_morae() でちょうど1モーラに分割されなかった "
            f"（実際は{len(morae)}モーラ）— 1 note は単一 Mora のみ表現できる（メリスマ非対応、"
            "score.py の「1 モーラ = 1 ノート」制約の機械強制）"
        )

# P0 は「同一 score・同一 lyrics・中央音域・表現指定を最小化」（§15）—
# 中央音域を MIDI 57-72 に固定する（score.py の in 音階 A3=57 を主音と
# する build_sakura_score() 全音域が MIDI 57-69 でこの帯に収まる）。
_P0_MIDI_LOW = 57
_P0_MIDI_HIGH = 72

# P5 は「学習分布外寄りだが baseline domain 内」（§15）— baseline domain
# を MIDI 45-90 に固定し、P0 域より外周（<57 または >72）の note を
# 少なくとも1つ含むことを要求する。
_P5_MIDI_LOW = 45
_P5_MIDI_HIGH = 90

_P3_DIAGNOSTIC_ROLE_MARKER = "diagnostic_when_trf_uncalibrated"

_RENDER_CONTRACT_KEYS: FrozenSet[str] = frozenset({
    "harness", "backbone_ref", "performance_seed", "performance_seed_note",
    "same_conditions_note", "pcm_publication_discipline", "harness_runtime_seed_policy",
})
_RENDER_CONTRACT_HARNESS = "voice_genesis/foundry/s1_gate/gate_synth.py::run_pipeline"
_BACKBONE_REF_KEYS: FrozenSet[str] = frozenset({"contract_path", "contract_field"})
_BACKBONE_REF_CONTRACT_PATH = "voice_genesis/evolution/run9_dual_founder_pjs/RUN9_CONTRACT.yaml"
_BACKBONE_REF_CONTRACT_FIELD = "backbone_runtime_bundle_sha"
# 学習 seed (909002) と混同しないことのマーカー（performance_seed_note が
# 保持しなければならない）。
_LEARNING_SEED_DISAMBIGUATION_MARKER = str(LEARNING_SEED)
# PR #322 第1巡指摘 Fix 1（P1, 採用）: `performance_seed` (909001) は
# genome/ControlProfile レベルの performance policy seed（v0.1 §9.3・
# founders/*.json `performance_seed` 欄・`validate_founder_genome()` が
# 厳格検証する対象）であり、宣言 harness（`gate_synth.py::run_pipeline`）
# 内部の ONNX runtime 乱数 seed ではない——両者を混同しないことの
# マーカー（`performance_seed_note` が保持しなければならない）。
_PERFORMANCE_SEED_GENOME_POLICY_MARKER = "performance policy seed"
_PERFORMANCE_SEED_NOT_ONNX_RUNTIME_MARKER = "ONNX runtime の乱数 seed ではない"
# §27 item 13「shared performance seed is identical」/ item 18「birth
# probes use same score/seed/ExecutionProfile」の参照マーカー。
_RENDER_CONTRACT_SECTION27_MARKER = "§27"
_RENDER_CONTRACT_ITEM13_MARKER = "item 13"
_RENDER_CONTRACT_ITEM18_MARKER = "item 18"
# PR #322 第1巡指摘 Fix 1: item 13/18 の「same seed」は genome-policy 層
# (909001) と harness-runtime 層 (42) の両方で、両 founder 間では同一値
# が使われることを指す——`same_conditions_note` がこの二層整合注記を
# 保持しなければならないマーカー（両方の値自体を含むことで確認する）。
_RENDER_CONTRACT_SAME_SEED_BOTH_LAYERS_MARKERS: Tuple[str, ...] = (
    str(SHARED_PERFORMANCE_SEED), "42",
)
# §15 末尾の PCM publication 規律（逐語順序）。
_PCM_PUBLICATION_DISCIPLINE_MARKERS: Tuple[str, ...] = (
    "float output", "PCM publication", "file readback", "meter", "actual WAV sha256",
)

# ---------------------------------------------------------------------------
# harness_runtime_seed_policy（PR #322 第1巡指摘 Fix 1, P1, 採用）:
# 宣言 harness `gate_synth.py::run_pipeline` は seed 引数を持たず、自身の
# ハードコード定数 `SEED = 42`（gate_synth.py:149）を `ort.set_seed(SEED)`
# / `record["seed"] = SEED`（gate_synth.py:1213-1214）で適用・記録する。
# gate_synth.py は RUN6/7/8 と共用の凍結計器であり、過去 run の provenance
# を壊さないため**改変は不採用**——本節は「909001 を runtime seed へ配線
# する」のではなく、実挙動（42）を manifest 側の宣言として真実化する
# 設計判定を記録する。42 は manifest が独自に選んだ値ではなく harness
# バイトの一部であり、run 開始時に `repository_commit_sha` が repo バイト
# 全体を pin することで凍結される（replay 契約 = 同一 harness バイト →
# 同一 seed）。
# ---------------------------------------------------------------------------
_HARNESS_RUNTIME_SEED_POLICY_KEYS: FrozenSet[str] = frozenset({
    "harness_hardcoded_seed", "harness_hardcoded_seed_source", "freeze_basis",
    "runtime_verification_condition", "no_wiring_declaration",
})
# gate_synth.py 実コードから転記した定数値（`SEED = 42`）。
_GATE_SYNTH_HARDCODED_SEED = 42
_HARNESS_HARDCODED_SEED_SOURCE_MARKERS: Tuple[str, ...] = ("gate_synth.py:149", "1213-1214")
_HARNESS_FREEZE_BASIS_MARKER = "repository_commit_sha"
_HARNESS_RUNTIME_VERIFICATION_MARKERS: Tuple[str, ...] = ("fail-closed", "42")
_HARNESS_NO_WIRING_DECLARATION_MARKER = "配線する変更は行わない"

_REVISION_BRIDGE_ENTRY_NAMES: Tuple[str, ...] = (
    "reference_render", "c0_replay_takes", "c1_sham_takes", "positive_reference",
    "negative_reference", "pjs_reference", "evaluated_renders",
)
# True のエントリは P0 cell を使った新規 render を要求する（`cell_ref`
# 必須）。False のエントリは新規 render 不要（`cell_ref` を持たない —
# 他方 founder の既存 reference_render / confuser_control の決定論的
# コーパス集約を参照するのみ）。
_REVISION_BRIDGE_NEW_RENDER_REQUIRED: Mapping[str, bool] = types.MappingProxyType({
    "reference_render": True,
    "c0_replay_takes": True,
    "c1_sham_takes": True,
    "positive_reference": True,
    "negative_reference": False,
    "pjs_reference": False,
    "evaluated_renders": True,
})
# c0/c1 のみ RUN9_CONTRACT.yaml interventions 配下のテイク数 pin 欄
# （INTERVENTION_TAKE_COUNT_FIELDS）へフィールド名参照する
# `contract_field_ref` を追加で要求する。
_REVISION_BRIDGE_CONTRACT_FIELD_REF: Mapping[str, str] = types.MappingProxyType({
    "c0_replay_takes": "RUN9_CONTRACT.yaml#interventions.c0_replay_takes_per_founder",
    "c1_sham_takes": "RUN9_CONTRACT.yaml#interventions.c1_sham_takes_per_founder",
})
_REVISION_BRIDGE_NO_NEW_RENDER_MARKER = "新規render不要"
_IDENTITY_METRIC_SPACE_REF_PREFIX = "inputs/identity_metric_space.json#"

# PR #322 第4巡指摘 Fix 8（P2, 採用）: 第2巡 Fix 5 の dotted path 走査は
# 「参照先が実在するか」しか証明せず、「その参照が“この”エントリの
# 意図する定義を指しているか」までは見ていなかった——2エントリ間で
# （どちらも実在する）path を入れ替えても通過してしまう欠陥だった。
# 本 dict が7エントリそれぞれの `identity_metric_space_ref` が指すべき
# 正確な dotted path を凍結する「エントリ→期待 path」の厳密対応表
# （実在走査に加えて厳密一致も要求する）。amendment で参照先の path 自体
# を変更する場合は、本対応表の更新が同時に必要——これは意図的な二重 pin
# （manifest 側の値変更だけでは通らない摩擦）であり、片方だけの更新で
# 「新しい path」が黙って別エントリへ流用されることを防ぐ。
_REVISION_BRIDGE_EXPECTED_METRIC_REF: Mapping[str, str] = types.MappingProxyType({
    "reference_render": (
        _IDENTITY_METRIC_SPACE_REF_PREFIX + "calibration.distance_unit.reference_render_definition"
    ),
    "c0_replay_takes": (
        _IDENTITY_METRIC_SPACE_REF_PREFIX + "calibration.freeze_threshold.d_c0_population"
    ),
    "c1_sham_takes": (
        _IDENTITY_METRIC_SPACE_REF_PREFIX + "calibration.validity_gates.c1_gate.d_c1_population"
    ),
    "positive_reference": (
        _IDENTITY_METRIC_SPACE_REF_PREFIX
        + "calibration.validity_gates.positive_reference_gate.positive_reference_definition"
    ),
    "negative_reference": (
        _IDENTITY_METRIC_SPACE_REF_PREFIX
        + "calibration.validity_gates.negative_reference_gate.negative_reference_definition"
    ),
    "pjs_reference": _IDENTITY_METRIC_SPACE_REF_PREFIX + "confuser_control.pjs_reference_definition",
    "evaluated_renders": _IDENTITY_METRIC_SPACE_REF_PREFIX + "identity_feature.scope",
})

_MEASUREMENT_BOUNDARY_KEYS: FrozenSet[str] = frozenset(
    {"scope_statement", "identity_axis_source", "development_generalization_axis_source"}
)
_MEASUREMENT_BOUNDARY_SCOPE_MARKERS: Tuple[str, ...] = ("何を鳴らすか", "どう測るかは対象外")
_MEASUREMENT_BOUNDARY_IDENTITY_AXIS_MARKERS: Tuple[str, ...] = (
    "inputs/identity_metric_space.json", "metric_space_sha",
)
_MEASUREMENT_BOUNDARY_DEV_GEN_AXIS_MARKERS: Tuple[str, ...] = ("measurement_spec_sha", "PENDING")

_PROHIBITION_MARKERS: Tuple[str, ...] = (
    "render後のcell",
    "結果を見た後のprobe変更",
    "測定仕様の変更を本manifestで行わない",
    "render不能cellの是正repin",
)
# render 不能 cell の是正 repin は「結果を見た後の水増し」禁止の対象外
# ——この区別（carve-out）自体を文言として要求する（項目8）。
_PROHIBITION_RENDER_INFEASIBLE_CARVEOUT_MARKERS: Tuple[str, ...] = ("水増し", "対象外")

_HELDOUT_INDEPENDENCE_KEYS: FrozenSet[str] = frozenset({"status", "independent_of", "note"})
HELDOUT_INDEPENDENCE_STATUS = "AUTHORED_INDEPENDENTLY_OF_PJS_CORPUS"


def _require_probe_int(value: Any, *, field: str) -> int:
    """bool を除外した厳密 int（`pitch_midi`/`phrase_index` 用。
    `_is_strict_int()` を再利用する — bool は int のサブクラスのため
    素の `isinstance(value, int)` では `True`/`False` を誤って許可して
    しまう。"""
    if not _is_strict_int(value):
        raise Run9ValidationError(
            f"{field} must be an exact int (bool/float/str/None rejected), got {value!r} "
            f"({type(value).__name__})"
        )
    return value


def _require_probe_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise Run9ValidationError(f"{field} must be exactly a bool, got {value!r}")
    return value


def _validate_probe_note(note: Any, *, field: str, phoneme_jp_module: Any) -> Dict[str, Any]:
    if not isinstance(note, dict):
        raise Run9ValidationError(f"{field} must be an object, got {type(note).__name__}")
    unknown = set(note.keys()) - _NOTE_KEYS
    if unknown:
        raise Run9ValidationError(f"{field} has unknown key(s): {sorted(unknown)}")
    missing = _NOTE_KEYS - set(note.keys())
    if missing:
        raise Run9ValidationError(f"{field} missing required key(s): {sorted(missing)}")
    kana = _require_non_empty_str(note["kana"], field=f"{field}.kana")
    # PR #322 第3巡指摘 Fix 6（P2, 採用）: renderer の mora 文法（唯一の
    # 正本、read-only 参照）に対し「ちょうど1モーラ」であることを全 note
    # で検証する（P2/P3 のクラス表対象外の note も含む）。
    _require_single_mora_kana(kana, phoneme_jp_module=phoneme_jp_module, field=f"{field}.kana")
    _require_probe_int(note["pitch_midi"], field=f"{field}.pitch_midi")
    _require_positive_finite_number(note["duration_beats"], field=f"{field}.duration_beats")
    phrase_index = _require_probe_int(note["phrase_index"], field=f"{field}.phrase_index")
    if phrase_index < 0:
        raise Run9ValidationError(f"{field}.phrase_index must be >= 0, got {phrase_index!r}")
    _require_probe_bool(note["is_phrase_final"], field=f"{field}.is_phrase_final")
    return note


def _validate_probe_cell_source(
    source: Any, *, field: str, score_path: Optional[Path] = None
) -> None:
    """`score_path` はテスト用の依存性注入点（PR #322 第2巡指摘 Fix 4）—
    省略時（`None`）は呼び出しのたびにモジュールレベル定数
    `SCORE_PY_REFERENCE_PATH`（凍結・改変禁止の read-only 参照）を都度
    参照する（デフォルト引数値として def 時に束縛すると、テストが
    `run9_schema.SCORE_PY_REFERENCE_PATH` を monkeypatch しても本関数の
    既定値には反映されない late-binding の罠を避けるため、あえて `None`
    センチネル + 関数本体内解決にしている）。実 score.py の rename/削除は
    一切行わない。"""
    if score_path is None:
        score_path = SCORE_PY_REFERENCE_PATH
    if not isinstance(source, dict):
        raise Run9ValidationError(f"{field} must be an object, got {type(source).__name__}")
    unknown = set(source.keys()) - _CELL_SOURCE_KEYS
    if unknown:
        raise Run9ValidationError(f"{field} has unknown key(s): {sorted(unknown)}")
    missing = _CELL_SOURCE_KEYS - set(source.keys())
    if missing:
        raise Run9ValidationError(f"{field} missing required key(s): {sorted(missing)}")
    transcribed_from = _require_non_empty_str(
        source["transcribed_from"], field=f"{field}.transcribed_from"
    )
    if transcribed_from != "voice_genesis/singer/score.py":
        raise Run9ValidationError(
            f"{field}.transcribed_from must be exactly 'voice_genesis/singer/score.py', "
            f"got {transcribed_from!r}"
        )
    declared_sha = source["transcribed_from_sha256"]
    if not isinstance(declared_sha, str) or not _SHA256_HEX_RE.match(declared_sha):
        raise Run9ValidationError(
            f"{field}.transcribed_from_sha256 must be exactly 64 lowercase hex characters, "
            f"got {declared_sha!r}"
        )
    # PR #322 第2巡指摘 Fix 4（P2, 採用）: 転記元ファイル不在を
    # fail-closed とする（旧実装は `score_path.is_file()` が False の
    # ときに照合そのものをスキップし、64hex 形式でさえあれば値を無条件に
    # 受理していた——installed/部分アーティファクト環境で P0 の
    # byte-verified 主張が無音で失われる欠陥だった）。本 validator は
    # repo checkout 内での実行を前提とし、転記元 score.py の実在 + hash
    # 一致が P0 受理の必須条件である。
    if not score_path.is_file():
        raise Run9ValidationError(
            f"{field}: pinned P0 transcription source {score_path} does not exist — this validator "
            "requires running from within a full repo checkout where the frozen read-only reference "
            "voice_genesis/singer/score.py is present; existence + hash equality against this file "
            "is a mandatory precondition for P0 acceptance (cannot verify a byte-verified verbatim "
            "transcription claim without the source file to verify it against)"
        )
    actual_sha = compute_file_sha256(score_path)
    if declared_sha != actual_sha:
        raise Run9ValidationError(
            f"{field}.transcribed_from_sha256 ({declared_sha!r}) does not match the actual raw "
            f"sha256 of {score_path} ({actual_sha!r}) — the P0 transcription source must stay "
            "byte-verified against the frozen read-only reference"
        )
    _require_non_empty_str(source["transcription_scope"], field=f"{field}.transcription_scope")
    if source["verbatim"] is not True:
        raise Run9ValidationError(f"{field}.verbatim must be exactly True, got {source['verbatim']!r}")


def _validate_cell_levels(value: Any, *, field: str) -> Dict[str, str]:
    """PR #322 第1巡指摘 Fix 2: cell の `levels` メタデータ（axis_name ->
    level_name の str->str 対応）の構造検証のみを行う。`factor_levels.axes`
    への実在照合は probe 単位（`_validate_probe_object`）でまとめて行う
    ——cell 単体では同じ probe の factor_levels にアクセスできないため。
    """
    if not isinstance(value, dict) or not value:
        raise Run9ValidationError(f"{field} must be a non-empty object, got {value!r}")
    for axis_name, level_name in value.items():
        if not isinstance(axis_name, str) or not axis_name.strip():
            raise Run9ValidationError(f"{field} has a non-string/empty axis key: {axis_name!r}")
        if not isinstance(level_name, str) or not level_name.strip():
            raise Run9ValidationError(
                f"{field}.{axis_name} must be a non-empty string level name, got {level_name!r}"
            )
    return value


def _validate_probe_cell(
    cell: Any, *, probe_id: str, field: str, seen_cell_ids: Dict[str, str], phoneme_jp_module: Any
) -> Optional[Dict[str, str]]:
    if not isinstance(cell, dict):
        raise Run9ValidationError(f"{field} must be an object, got {type(cell).__name__}")
    allowed = set(_CELL_KEYS_BASE)
    required = set(_CELL_KEYS_BASE)
    if probe_id == "P0":
        allowed.add(_CELL_SOURCE_KEY)
        required.add(_CELL_SOURCE_KEY)
    if probe_id in _FACTOR_LEVEL_PROBE_IDS:
        allowed.add(_CELL_LEVELS_KEY)
        required.add(_CELL_LEVELS_KEY)
    unknown = set(cell.keys()) - allowed
    if unknown:
        raise Run9ValidationError(f"{field} has unknown key(s): {sorted(unknown)}")
    missing = required - set(cell.keys())
    if missing:
        raise Run9ValidationError(f"{field} missing required key(s): {sorted(missing)}")

    cell_id = _require_non_empty_str(cell["cell_id"], field=f"{field}.cell_id")
    if cell_id in seen_cell_ids:
        raise Run9ValidationError(
            f"{field}.cell_id {cell_id!r} duplicates the cell_id already used by "
            f"{seen_cell_ids[cell_id]!r} — cell_id must be unique across the entire manifest"
        )
    seen_cell_ids[cell_id] = field

    _require_positive_finite_number(cell["tempo_bpm"], field=f"{field}.tempo_bpm")

    notes = cell["notes"]
    if not isinstance(notes, list) or not notes:
        raise Run9ValidationError(f"{field}.notes must be a non-empty list, got {notes!r}")
    for i, note in enumerate(notes):
        validated = _validate_probe_note(
            note, field=f"{field}.notes[{i}]", phoneme_jp_module=phoneme_jp_module
        )
        pitch = validated["pitch_midi"]
        if probe_id == "P0" and not (_P0_MIDI_LOW <= pitch <= _P0_MIDI_HIGH):
            raise Run9ValidationError(
                f"{field}.notes[{i}].pitch_midi = {pitch!r} is outside the P0 central-register "
                f"domain [{_P0_MIDI_LOW}, {_P0_MIDI_HIGH}] (DESIGN_RUN9 §15 P0: 中央音域)"
            )
        if probe_id == "P5" and not (_P5_MIDI_LOW <= pitch <= _P5_MIDI_HIGH):
            raise Run9ValidationError(
                f"{field}.notes[{i}].pitch_midi = {pitch!r} is outside the P5 baseline domain "
                f"[{_P5_MIDI_LOW}, {_P5_MIDI_HIGH}] (DESIGN_RUN9 §15 P5: baseline domain 内)"
            )

    if probe_id == "P0":
        _validate_probe_cell_source(cell[_CELL_SOURCE_KEY], field=f"{field}.{_CELL_SOURCE_KEY}")

    if probe_id in _FACTOR_LEVEL_PROBE_IDS:
        return _validate_cell_levels(cell[_CELL_LEVELS_KEY], field=f"{field}.{_CELL_LEVELS_KEY}")
    return None


def _validate_factor_levels_axes(data: Any, *, field: str) -> Dict[str, Dict[str, Any]]:
    """PR #322 第1巡指摘 Fix 2: `factor_levels.axes` の形状・型検証。各軸は
    非空 dict（水準名 -> 具体値）、水準名は非空 str、値は bool を除く
    実数（register/duration 等の数値水準）または非空 str（onset
    consonant class・ending voicing 等の記号水準）のいずれか——空文字列・
    空 list/dict・None・bool は拒否する。"""
    if not isinstance(data, dict) or not data:
        raise Run9ValidationError(f"{field} must be a non-empty object, got {data!r}")
    for axis_name, levels in data.items():
        if not isinstance(axis_name, str) or not axis_name.strip():
            raise Run9ValidationError(f"{field} has a non-string/empty axis key: {axis_name!r}")
        axis_field = f"{field}.{axis_name}"
        if not isinstance(levels, dict) or not levels:
            raise Run9ValidationError(f"{axis_field} must be a non-empty object, got {levels!r}")
        for level_name, level_value in levels.items():
            if not isinstance(level_name, str) or not level_name.strip():
                raise Run9ValidationError(
                    f"{axis_field} has a non-string/empty level key: {level_name!r}"
                )
            level_field = f"{axis_field}.{level_name}"
            if isinstance(level_value, bool):
                raise Run9ValidationError(f"{level_field} must not be a bool, got {level_value!r}")
            is_number = isinstance(level_value, (int, float)) and math.isfinite(level_value)
            is_nonempty_str = isinstance(level_value, str) and bool(level_value.strip())
            if not (is_number or is_nonempty_str):
                raise Run9ValidationError(
                    f"{level_field} must be a finite non-bool number or a non-empty string "
                    f"(concrete stimulus value), got {level_value!r}"
                )
    return data


def _numeric_equal(a: Any, b: Any) -> bool:
    """bool を除外した int/float 同士の厳密等値。"""
    return (
        isinstance(a, (int, float)) and not isinstance(a, bool)
        and isinstance(b, (int, float)) and not isinstance(b, bool)
        and a == b
    )


def _select_phrase_final_note(cell: Mapping[str, Any], *, field: str) -> Dict[str, Any]:
    """PR #322 第2巡指摘 Fix 3: 軸別意味照合の対象 note（`is_phrase_final:
    true` の note）をちょうど1つに限定して返す。P1 の単一 note cell では
    その唯一の note、P2/P3 の複数 note cell では終端/target note を指す
    ——note 位置のインデックス（先頭/末尾）ではなく `is_phrase_final`
    マーカーそのものを根拠にする。"""
    finals = [n for n in cell["notes"] if n.get("is_phrase_final") is True]
    if len(finals) != 1:
        raise Run9ValidationError(
            f"{field}: cell {cell.get('cell_id')!r} must have exactly one note with "
            f"is_phrase_final=true to serve as the semantic target for axis-value checking "
            f"(Fix 3), got {len(finals)}"
        )
    return finals[0]


def _check_axis_numeric_field(
    cell: Mapping[str, Any], *, field_name: str, expected: Any, field: str, axis_name: str,
    level_name: str,
) -> None:
    note = _select_phrase_final_note(cell, field=field)
    actual = note[field_name]
    if not _numeric_equal(actual, expected):
        raise Run9ValidationError(
            f"{field}: cell {cell.get('cell_id')!r} declares levels.{axis_name}={level_name!r} "
            f"(factor_levels.axes.{axis_name}.{level_name} = {expected!r}) but the phrase-final "
            f"note's {field_name} = {actual!r} — declared level does not match the rendered stimulus"
        )


def _check_axis_kana_class(
    cell: Mapping[str, Any], *, table: Mapping[str, str], field: str, axis_name: str, level_name: str,
) -> None:
    note = _select_phrase_final_note(cell, field=field)
    kana = note["kana"]
    if kana not in table:
        raise Run9ValidationError(
            f"{field}: cell {cell.get('cell_id')!r} phrase-final note kana {kana!r} is not in the "
            f"frozen {axis_name} kana table ({sorted(table)}) — cannot verify declared level "
            f"{level_name!r}"
        )
    actual_class = table[kana]
    if actual_class != level_name:
        raise Run9ValidationError(
            f"{field}: cell {cell.get('cell_id')!r} declares levels.{axis_name}={level_name!r} but "
            f"phrase-final note kana {kana!r} maps to {actual_class!r} in the frozen {axis_name} "
            "kana table — declared level does not match the rendered stimulus"
        )


def _check_axis_transition_direction(
    cell: Mapping[str, Any], *, expected: Any, field: str, axis_name: str, level_name: str,
) -> None:
    notes = cell["notes"]
    if len(notes) < 2:
        raise Run9ValidationError(
            f"{field}: cell {cell.get('cell_id')!r} declares levels.{axis_name}={level_name!r} but "
            f"has fewer than 2 notes ({len(notes)}) — a transition needs a start and end note"
        )
    actual = f"{notes[0]['pitch_midi']}->{notes[-1]['pitch_midi']}"
    if not isinstance(expected, str) or actual != expected:
        raise Run9ValidationError(
            f"{field}: cell {cell.get('cell_id')!r} declares levels.{axis_name}={level_name!r} "
            f"(factor_levels.axes.{axis_name}.{level_name} = {expected!r}) but the actual first->last "
            f"note pitch_midi sequence is {actual!r} — declared level does not match the rendered "
            "stimulus"
        )


def _check_axis_phrase_dynamics_structure(
    cell: Mapping[str, Any], *, field: str, axis_name: str, level_name: str,
) -> None:
    """phrase_dynamics（弱→強）: note schema に velocity/dynamics 欄が
    存在しないため、構造（非減少 pitch 系列 + phrase-final note が末尾）
    の実在を意味照合の代替とする。"""
    notes = cell["notes"]
    final = _select_phrase_final_note(cell, field=field)
    if len(notes) < 2 or notes[-1] is not final:
        raise Run9ValidationError(
            f"{field}: cell {cell.get('cell_id')!r} declares levels.{axis_name}={level_name!r} but "
            "does not have >= 2 notes ending with the phrase-final note — a weak->strong build "
            "requires the phrase-final (strong) note to be the last note in the sequence"
        )
    pitches = [n["pitch_midi"] for n in notes]
    if any(pitches[i] > pitches[i + 1] for i in range(len(pitches) - 1)):
        raise Run9ValidationError(
            f"{field}: cell {cell.get('cell_id')!r} declares levels.{axis_name}={level_name!r} "
            "which requires a non-decreasing pitch contour across notes (the structural proxy for "
            f"weak->strong used in the absence of a velocity/dynamics field), got pitches {pitches!r}"
        )


def _validate_axis_semantic_value(
    *, axis_name: str, level_name: str, axis_value: Any, cell: Mapping[str, Any], field: str,
) -> None:
    """PR #322 第2巡指摘 Fix 3（P2, 採用）の実装: cell が参照する
    (axis_name, level_name) について、factor_levels.axes が宣言する
    具体値 (`axis_value`) が cell の実 note フィールドと一致することを
    照合する。未登録の axis は fail-closed で拒否する（新しい軸を追加した
    のに対応する意味照合を追加し忘れる事故を構造的に防ぐ）。"""
    if axis_name in _AXIS_NUMERIC_FIELD_CHECKS:
        _check_axis_numeric_field(
            cell, field_name=_AXIS_NUMERIC_FIELD_CHECKS[axis_name], expected=axis_value, field=field,
            axis_name=axis_name, level_name=level_name,
        )
    elif axis_name in _AXIS_KANA_CLASS_CHECKS:
        _check_axis_kana_class(
            cell, table=_AXIS_KANA_CLASS_CHECKS[axis_name], field=field, axis_name=axis_name,
            level_name=level_name,
        )
    elif axis_name == "transition_direction":
        _check_axis_transition_direction(
            cell, expected=axis_value, field=field, axis_name=axis_name, level_name=level_name
        )
    elif axis_name == "phrase_dynamics":
        _check_axis_phrase_dynamics_structure(
            cell, field=field, axis_name=axis_name, level_name=level_name
        )
    else:
        raise Run9ValidationError(
            f"{field}: no axis-specific semantic checker is registered for axis {axis_name!r} (Fix 3 "
            "requires every declared factor_levels axis to have a checker comparing the declared "
            "level value against the actual rendered stimulus — an unregistered axis would silently "
            "accept a repin that changes the notes without updating the label)"
        )


def _validate_probe_factor_levels_cell_mapping(
    *, factor_levels: Any, cells: List[Dict[str, Any]], field: str
) -> None:
    """PR #322 第1巡指摘 Fix 2 + 第2巡指摘 Fix 3: `factor_levels.axes` と
    各 cell の `levels` の双方向対応（ラベル実在）+ 軸別の意味照合
    （宣言された具体値と cell の実 note フィールドの一致）を検証する。
    前方（cell -> factor_levels 実在確認）+ 後方（factor_levels の全水準
    が最低1 cell で使用されているか——未使用水準は宣言と刺激の乖離として
    拒否）の両方向のラベル対応に加え、参照された水準の具体値そのものが
    実際に render される note と一致するかを軸別 checker
    （`_validate_axis_semantic_value()`）で照合する。個々の cell は
    factor_levels の全軸を参照する必要はない（部分参照可 — 例: P1 の
    音程遷移 cell は register/duration 軸を参照しない）。"""
    if not isinstance(factor_levels, dict):
        raise Run9ValidationError(f"{field} must be an object, got {type(factor_levels).__name__}")
    if _FACTOR_LEVELS_AXES_KEY not in factor_levels:
        raise Run9ValidationError(f"{field} missing required key(s): ['{_FACTOR_LEVELS_AXES_KEY}']")
    axes = _validate_factor_levels_axes(
        factor_levels[_FACTOR_LEVELS_AXES_KEY], field=f"{field}.{_FACTOR_LEVELS_AXES_KEY}"
    )

    used: Dict[str, set] = {axis_name: set() for axis_name in axes}
    for i, cell in enumerate(cells):
        levels = cell[_CELL_LEVELS_KEY]
        for axis_name, level_name in levels.items():
            if axis_name not in axes:
                raise Run9ValidationError(
                    f"cells[{i}].{_CELL_LEVELS_KEY} references unknown axis {axis_name!r} — not "
                    f"declared in {field}.{_FACTOR_LEVELS_AXES_KEY} ({sorted(axes)})"
                )
            if level_name not in axes[axis_name]:
                raise Run9ValidationError(
                    f"cells[{i}].{_CELL_LEVELS_KEY}.{axis_name} references unknown level "
                    f"{level_name!r} — not declared in {field}.{_FACTOR_LEVELS_AXES_KEY}.{axis_name} "
                    f"({sorted(axes[axis_name])})"
                )
            used[axis_name].add(level_name)
            _validate_axis_semantic_value(
                axis_name=axis_name, level_name=level_name, axis_value=axes[axis_name][level_name],
                cell=cell, field=f"cells[{i}]",
            )

    for axis_name, levels in axes.items():
        unused = set(levels) - used[axis_name]
        if unused:
            raise Run9ValidationError(
                f"{field}.{_FACTOR_LEVELS_AXES_KEY}.{axis_name} declares level(s) {sorted(unused)} "
                "that no cell's `levels` references — an unused declared level is a drift between "
                "the frozen experimental axis table and what is actually rendered"
            )


def _validate_probe_heldout_independence(value: Any, *, field: str) -> None:
    if not isinstance(value, dict):
        raise Run9ValidationError(f"{field} must be an object, got {type(value).__name__}")
    unknown = set(value.keys()) - _HELDOUT_INDEPENDENCE_KEYS
    if unknown:
        raise Run9ValidationError(f"{field} has unknown key(s): {sorted(unknown)}")
    missing = _HELDOUT_INDEPENDENCE_KEYS - set(value.keys())
    if missing:
        raise Run9ValidationError(f"{field} missing required key(s): {sorted(missing)}")
    status = value["status"]
    if status != HELDOUT_INDEPENDENCE_STATUS:
        raise Run9ValidationError(
            f"{field}.status must be exactly {HELDOUT_INDEPENDENCE_STATUS!r}, got {status!r}"
        )
    _require_nonempty_str_list(
        value["independent_of"], manifest_kind="probe manifest", field=f"{field}.independent_of"
    )
    _require_non_empty_str(value["note"], field=f"{field}.note")


def _validate_p2_onset_filler_consistency(
    *, factor_levels: Mapping[str, Any], cells: List[Dict[str, Any]], field: str
) -> None:
    """PR #322 第3巡指摘 Fix 7（P2, 採用）の実装: `factor_levels` が宣言
    する凍結 filler タプル（`medial_filler_kana`/`medial_filler_beats`/
    `medial_filler_pitch_midi`）を検証し、`onset_consonant_class` 軸を
    持つ全 cell（onset cell）の前置 note 列（検定 note = phrase-final note
    より前の全 note）がこのタプルと完全一致すること——結果として全 onset
    cell 間で filler が同一であること——を機械強制する。"""
    missing = _P2_FILLER_TUPLE_KEYS - set(factor_levels.keys())
    if missing:
        raise Run9ValidationError(
            f"{field} missing required P2 filler declaration key(s): {sorted(missing)}"
        )
    filler_kana = _require_non_empty_str(
        factor_levels["medial_filler_kana"], field=f"{field}.medial_filler_kana"
    )
    filler_beats = _require_positive_finite_number(
        factor_levels["medial_filler_beats"], field=f"{field}.medial_filler_beats"
    )
    filler_pitch = _require_probe_int(
        factor_levels["medial_filler_pitch_midi"], field=f"{field}.medial_filler_pitch_midi"
    )

    for cell in cells:
        levels = cell.get(_CELL_LEVELS_KEY, {})
        if not isinstance(levels, dict) or _P2_ONSET_AXIS_NAME not in levels:
            continue  # onset_consonant_class 軸を参照しない cell（P2-PHRASE-BUILD 等）は対象外
        cell_id = cell.get("cell_id")
        notes = cell["notes"]
        final = _select_phrase_final_note(cell, field=f"{field} (cell_id={cell_id!r})")
        final_idx = next(i for i, n in enumerate(notes) if n is final)
        prefix = notes[:final_idx]
        if len(prefix) != 1:
            raise Run9ValidationError(
                f"{field}: onset cell {cell_id!r} must have exactly one prefix (filler) note before "
                f"the phrase-final target note, got {len(prefix)}"
            )
        p = prefix[0]
        if (
            p["kana"] != filler_kana
            or not _numeric_equal(p["duration_beats"], filler_beats)
            or not _numeric_equal(p["pitch_midi"], filler_pitch)
        ):
            raise Run9ValidationError(
                f"{field}: onset cell {cell_id!r} prefix note "
                f"(kana={p['kana']!r}, duration_beats={p['duration_beats']!r}, "
                f"pitch_midi={p['pitch_midi']!r}) does not match the frozen P2 filler tuple "
                f"(kana={filler_kana!r}, duration_beats={filler_beats!r}, pitch_midi={filler_pitch!r}"
                ") declared in factor_levels — all onset cells must share the identical filler "
                "pre-context so the onset-class comparison is not confounded by differing context"
            )


def _validate_probe_object(
    probe: Any, *, expected_probe_id: str, field: str, seen_cell_ids: Dict[str, str],
    phoneme_jp_module: Any,
) -> None:
    if not isinstance(probe, dict):
        raise Run9ValidationError(f"{field} must be an object, got {type(probe).__name__}")
    extra = _PROBE_REQUIRED_EXTRA_KEYS[expected_probe_id]
    allowed = set(_PROBE_BASE_KEYS) | extra
    unknown = set(probe.keys()) - allowed
    if unknown:
        raise Run9ValidationError(f"{field} has unknown key(s): {sorted(unknown)}")
    missing = allowed - set(probe.keys())
    if missing:
        raise Run9ValidationError(f"{field} missing required key(s): {sorted(missing)}")

    if probe["probe_id"] != expected_probe_id:
        raise Run9ValidationError(
            f"{field}.probe_id must be exactly {expected_probe_id!r}, got {probe['probe_id']!r}"
        )
    if probe["title"] != PROBE_TITLES[expected_probe_id]:
        raise Run9ValidationError(
            f"{field}.title must be exactly {PROBE_TITLES[expected_probe_id]!r} (DESIGN_RUN9 §15 "
            f"逐語), got {probe['title']!r}"
        )
    if probe["design_source"] != PROBE_DESIGN_SOURCE:
        raise Run9ValidationError(
            f"{field}.design_source must be exactly {PROBE_DESIGN_SOURCE!r}, got "
            f"{probe['design_source']!r}"
        )
    role = _require_non_empty_str(probe["role"], field=f"{field}.role")
    if expected_probe_id == "P3" and _P3_DIAGNOSTIC_ROLE_MARKER not in role:
        raise Run9ValidationError(
            f"{field}.role must contain the marker {_P3_DIAGNOSTIC_ROLE_MARKER!r} (DESIGN_RUN9 §15 "
            "P3: TRF 未校正時は diagnostic/advisory の機械可読化), got role without the marker"
        )

    cells = probe["cells"]
    if not isinstance(cells, list) or not cells:
        raise Run9ValidationError(f"{field}.cells must be a non-empty list, got {cells!r}")
    for i, cell in enumerate(cells):
        _validate_probe_cell(
            cell, probe_id=expected_probe_id, field=f"{field}.cells[{i}]", seen_cell_ids=seen_cell_ids,
            phoneme_jp_module=phoneme_jp_module,
        )

    # PR #322 第4巡指摘 Fix 9（P2, 採用）: probe 別の期待 cell_id 集合
    # （閉じた集合）と厳密一致することを要求する——cell 削除/余剰追加の
    # いずれも fail-closed。
    _validate_probe_expected_cell_ids(expected_probe_id=expected_probe_id, cells=cells, field=field)

    if expected_probe_id in _FACTOR_LEVEL_PROBE_IDS:
        _validate_probe_factor_levels_cell_mapping(
            factor_levels=probe["factor_levels"], cells=cells, field=f"{field}.factor_levels"
        )

    if expected_probe_id in _PROBE_FACTORIAL_AXES:
        # Fix 9: full factorial 直積被覆（P1: register×duration, P3:
        # release_duration×ending_voicing）。
        _validate_probe_factorial_coverage(
            expected_probe_id=expected_probe_id, factor_levels=probe["factor_levels"], cells=cells,
            field=f"{field}.factor_levels",
        )

    if expected_probe_id == "P2":
        _validate_p2_onset_filler_consistency(
            factor_levels=probe["factor_levels"], cells=cells, field=f"{field}.factor_levels"
        )

    if expected_probe_id == "P5":
        pitches = [note["pitch_midi"] for cell in cells for note in cell["notes"]]
        if not any(p < _P0_MIDI_LOW or p > _P0_MIDI_HIGH for p in pitches):
            raise Run9ValidationError(
                f"{field}: P5 must include at least one note outside the P0 central-register domain "
                f"[{_P0_MIDI_LOW}, {_P0_MIDI_HIGH}] while staying within the P5 baseline domain "
                f"[{_P5_MIDI_LOW}, {_P5_MIDI_HIGH}] (DESIGN_RUN9 §15 P5: 学習分布外寄り)"
            )

    if expected_probe_id == "P4":
        _validate_probe_heldout_independence(
            probe["heldout_independence"], field=f"{field}.heldout_independence"
        )


def _validate_harness_runtime_seed_policy(data: Any) -> None:
    """PR #322 第1巡指摘 Fix 1（P1, 採用）の実装: 宣言 harness
    `gate_synth.py::run_pipeline` が実際に消費する runtime seed（自身の
    ハードコード定数 `SEED = 42`）を manifest 側に明示し、
    `performance_seed` (909001, genome/ControlProfile レベル) と混同
    しないことを機械強制する。gate_synth.py 自体の改変は不採用（RUN6/7/8
    と共用の凍結計器のため）——本 validator は宣言の真実化のみを検証する。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(
            f"render_contract.harness_runtime_seed_policy must be an object, got "
            f"{type(data).__name__}"
        )
    unknown = set(data.keys()) - _HARNESS_RUNTIME_SEED_POLICY_KEYS
    if unknown:
        raise Run9ValidationError(
            f"render_contract.harness_runtime_seed_policy has unknown key(s): {sorted(unknown)}"
        )
    missing = _HARNESS_RUNTIME_SEED_POLICY_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(
            f"render_contract.harness_runtime_seed_policy missing required key(s): {sorted(missing)}"
        )

    seed = data["harness_hardcoded_seed"]
    if not _is_strict_int(seed) or seed != _GATE_SYNTH_HARDCODED_SEED:
        raise Run9ValidationError(
            "render_contract.harness_runtime_seed_policy.harness_hardcoded_seed must be the exact "
            f"int {_GATE_SYNTH_HARDCODED_SEED!r} (bool/float variants rejected), got {seed!r} "
            f"({type(seed).__name__})"
        )

    source = _require_non_empty_str(
        data["harness_hardcoded_seed_source"],
        field="render_contract.harness_runtime_seed_policy.harness_hardcoded_seed_source",
    )
    for marker in _HARNESS_HARDCODED_SEED_SOURCE_MARKERS:
        if marker not in source:
            raise Run9ValidationError(
                "render_contract.harness_runtime_seed_policy.harness_hardcoded_seed_source must "
                f"contain the marker {marker!r} (実コードの行番号転記), got a source without that "
                "marker"
            )

    freeze_basis = _require_non_empty_str(
        data["freeze_basis"], field="render_contract.harness_runtime_seed_policy.freeze_basis"
    )
    if _HARNESS_FREEZE_BASIS_MARKER not in freeze_basis:
        raise Run9ValidationError(
            "render_contract.harness_runtime_seed_policy.freeze_basis must contain the marker "
            f"{_HARNESS_FREEZE_BASIS_MARKER!r} (42 は harness バイトの一部であり "
            "repository_commit_sha が repo バイト全体を pin することで凍結される), got a freeze_basis "
            "without that marker"
        )

    verification = _require_non_empty_str(
        data["runtime_verification_condition"],
        field="render_contract.harness_runtime_seed_policy.runtime_verification_condition",
    )
    for marker in _HARNESS_RUNTIME_VERIFICATION_MARKERS:
        if marker not in verification:
            raise Run9ValidationError(
                "render_contract.harness_runtime_seed_policy.runtime_verification_condition must "
                f"contain the marker {marker!r} (render record の SEED が 42 と不一致なら契約違反 "
                "として fail-closed とする、pod フェーズの検収条件の事前登録), got a condition "
                "without that marker"
            )

    no_wiring = _require_non_empty_str(
        data["no_wiring_declaration"],
        field="render_contract.harness_runtime_seed_policy.no_wiring_declaration",
    )
    if _HARNESS_NO_WIRING_DECLARATION_MARKER not in no_wiring:
        raise Run9ValidationError(
            "render_contract.harness_runtime_seed_policy.no_wiring_declaration must contain the "
            f"marker {_HARNESS_NO_WIRING_DECLARATION_MARKER!r} (909001 を runtime seed として "
            "harness へ配線する変更は行わない——宣言と実装の乖離は実挙動側を正とし宣言を真実化する "
            "設計判定), got a declaration without that marker"
        )


def _validate_render_contract(data: Any) -> None:
    if not isinstance(data, dict):
        raise Run9ValidationError(f"render_contract must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _RENDER_CONTRACT_KEYS
    if unknown:
        raise Run9ValidationError(f"render_contract has unknown key(s): {sorted(unknown)}")
    missing = _RENDER_CONTRACT_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"render_contract missing required key(s): {sorted(missing)}")

    if data["harness"] != _RENDER_CONTRACT_HARNESS:
        raise Run9ValidationError(
            f"render_contract.harness must be exactly {_RENDER_CONTRACT_HARNESS!r}, "
            f"got {data['harness']!r}"
        )

    backbone_ref = data["backbone_ref"]
    if not isinstance(backbone_ref, dict):
        raise Run9ValidationError(
            f"render_contract.backbone_ref must be an object, got {type(backbone_ref).__name__}"
        )
    unknown_ref = set(backbone_ref.keys()) - _BACKBONE_REF_KEYS
    if unknown_ref:
        raise Run9ValidationError(
            f"render_contract.backbone_ref has unknown key(s): {sorted(unknown_ref)}"
        )
    missing_ref = _BACKBONE_REF_KEYS - set(backbone_ref.keys())
    if missing_ref:
        raise Run9ValidationError(
            f"render_contract.backbone_ref missing required key(s): {sorted(missing_ref)}"
        )
    if backbone_ref["contract_path"] != _BACKBONE_REF_CONTRACT_PATH:
        raise Run9ValidationError(
            f"render_contract.backbone_ref.contract_path must be exactly "
            f"{_BACKBONE_REF_CONTRACT_PATH!r}, got {backbone_ref['contract_path']!r}"
        )
    if backbone_ref["contract_field"] != _BACKBONE_REF_CONTRACT_FIELD:
        raise Run9ValidationError(
            f"render_contract.backbone_ref.contract_field must be exactly "
            f"{_BACKBONE_REF_CONTRACT_FIELD!r} (field-name reference only — the pin value itself "
            f"must not be duplicated here), got {backbone_ref['contract_field']!r}"
        )

    seed = data["performance_seed"]
    if not _is_strict_int(seed) or seed != SHARED_PERFORMANCE_SEED:
        raise Run9ValidationError(
            f"render_contract.performance_seed must be the exact int {SHARED_PERFORMANCE_SEED!r} "
            f"(bool/float variants rejected), got {seed!r} ({type(seed).__name__})"
        )
    seed_note = _require_non_empty_str(
        data["performance_seed_note"], field="render_contract.performance_seed_note"
    )
    if _LEARNING_SEED_DISAMBIGUATION_MARKER not in seed_note:
        raise Run9ValidationError(
            "render_contract.performance_seed_note must mention the learning seed "
            f"({_LEARNING_SEED_DISAMBIGUATION_MARKER!r}) to disambiguate it from the shared "
            "performance seed, got a note without that marker"
        )
    for marker in (_PERFORMANCE_SEED_GENOME_POLICY_MARKER, _PERFORMANCE_SEED_NOT_ONNX_RUNTIME_MARKER):
        if marker not in seed_note:
            raise Run9ValidationError(
                f"render_contract.performance_seed_note must contain the marker {marker!r} (PR #322 "
                "第1巡指摘 Fix 1: 909001 は genome/ControlProfile レベルの performance policy seed "
                "であり、宣言 harness 内部の ONNX runtime 乱数 seed ではないことを明記する), got a "
                "note without that marker"
            )

    same_conditions_note = _require_non_empty_str(
        data["same_conditions_note"], field="render_contract.same_conditions_note"
    )
    for marker in (
        _RENDER_CONTRACT_SECTION27_MARKER, _RENDER_CONTRACT_ITEM13_MARKER, _RENDER_CONTRACT_ITEM18_MARKER,
    ):
        if marker not in same_conditions_note:
            raise Run9ValidationError(
                f"render_contract.same_conditions_note must contain the marker {marker!r} "
                "(DESIGN_RUN9 §27 item 13/18 参照マーカー), got a note without that marker"
            )
    for marker in _RENDER_CONTRACT_SAME_SEED_BOTH_LAYERS_MARKERS:
        if marker not in same_conditions_note:
            raise Run9ValidationError(
                f"render_contract.same_conditions_note must contain the marker {marker!r} (PR #322 "
                "第1巡指摘 Fix 1: item 13/18 の same-seed 要求は genome-policy 層 (909001) と "
                "harness-runtime 層 (42) の両方で両 founder 間同一であることを一言で確認する), got "
                "a note without that marker"
            )

    _validate_harness_runtime_seed_policy(data["harness_runtime_seed_policy"])

    discipline = _require_non_empty_str(
        data["pcm_publication_discipline"], field="render_contract.pcm_publication_discipline"
    )
    last_index = -1
    for marker in _PCM_PUBLICATION_DISCIPLINE_MARKERS:
        idx = discipline.find(marker)
        if idx == -1:
            raise Run9ValidationError(
                f"render_contract.pcm_publication_discipline must contain the marker {marker!r} "
                "(DESIGN_RUN9 §15 末尾 PCM publication 規律の逐語), got a discipline text without it"
            )
        if idx <= last_index:
            raise Run9ValidationError(
                "render_contract.pcm_publication_discipline markers must appear in the DESIGN_RUN9 "
                "§15 order (float output -> PCM publication -> file readback -> meter -> actual WAV "
                f"sha256) — {marker!r} appears out of order"
            )
        last_index = idx


def _load_identity_metric_space_document(*, path: Optional[Path] = None) -> Dict[str, Any]:
    """PR #322 第2巡指摘 Fix 5 用: `revision_bridge.*.identity_metric_space_ref`
    の dotted path 全体を実文書に対して走査するために、
    `inputs/identity_metric_space.json`（凍結・改変禁止の read-only 入力）
    を読み込むだけの loader。他の validator（`validate_identity_metric_
    space_manifest()`）と異なり、本関数は形状検証は行わず単に
    `_loads_strict_json()` でパースした dict を返す——形状検証は
    `validate_identity_metric_space_manifest()` の職務のまま重複させない。
    `path` 省略時（`None`）はモジュールレベル定数 `IDENTITY_METRIC_SPACE_
    PATH` を呼び出しのたびに参照する（`_validate_probe_cell_source()` と
    同じ late-binding 回避パターン）。
    """
    if path is None:
        path = IDENTITY_METRIC_SPACE_PATH
    if not path.is_file():
        raise Run9ValidationError(
            f"revision_bridge.*.identity_metric_space_ref の dotted path 解決には {path} の実在が "
            "必須だが見つからない（凍結・改変禁止の read-only 入力）"
        )
    return _loads_strict_json(path.read_text(encoding="utf-8"))


def _resolve_identity_metric_space_ref(
    ref: str, *, document: Mapping[str, Any], field: str
) -> None:
    """PR #322 第2巡指摘 Fix 5（P2, 採用）の実装: `inputs/identity_metric_
    space.json#a.b.c` 形式の参照の dotted path 全セグメントを実文書に
    対して走査し、途中の typo（例: `#calibration.does_not_exist`）を
    fail-closed で検出する。旧実装は先頭セグメントのみを閉じた語彙表と
    照合しており、深部・中間セグメントの typo は素通りしていた。"""
    suffix = ref[len(_IDENTITY_METRIC_SPACE_REF_PREFIX):]
    segments = suffix.split(".") if suffix else []
    if not segments or any(not s for s in segments):
        raise Run9ValidationError(
            f"{field} has a malformed dotted path suffix after "
            f"{_IDENTITY_METRIC_SPACE_REF_PREFIX!r}: {suffix!r}"
        )
    current: Any = document
    walked: List[str] = []
    for segment in segments:
        walked.append(segment)
        if not isinstance(current, Mapping) or segment not in current:
            raise Run9ValidationError(
                f"{field} = {ref!r} does not resolve against {IDENTITY_METRIC_SPACE_PATH} — segment "
                f"{segment!r} (path so far: {'.'.join(walked)!r}) does not exist"
            )
        current = current[segment]


def _validate_revision_bridge_entry(
    entry: Any, *, entry_name: str, field: str, valid_cell_ids: FrozenSet[str],
    identity_metric_space_document: Mapping[str, Any],
) -> None:
    if not isinstance(entry, dict):
        raise Run9ValidationError(f"{field} must be an object, got {type(entry).__name__}")
    requires_new_render = _REVISION_BRIDGE_NEW_RENDER_REQUIRED[entry_name]
    has_contract_field_ref = entry_name in _REVISION_BRIDGE_CONTRACT_FIELD_REF
    allowed = {"description", "identity_metric_space_ref", "new_render_required"}
    if requires_new_render:
        allowed.add("cell_ref")
    if has_contract_field_ref:
        allowed.add("contract_field_ref")
    unknown = set(entry.keys()) - allowed
    if unknown:
        raise Run9ValidationError(f"{field} has unknown key(s): {sorted(unknown)}")
    missing = allowed - set(entry.keys())
    if missing:
        raise Run9ValidationError(f"{field} missing required key(s): {sorted(missing)}")

    description = _require_non_empty_str(entry["description"], field=f"{field}.description")
    if not requires_new_render and _REVISION_BRIDGE_NO_NEW_RENDER_MARKER not in description:
        raise Run9ValidationError(
            f"{field}.description must contain the marker {_REVISION_BRIDGE_NO_NEW_RENDER_MARKER!r} "
            "(negative_reference/pjs_reference は新規 render 不要), got a description without that "
            "marker"
        )

    ref = entry["identity_metric_space_ref"]
    if not isinstance(ref, str) or not ref.startswith(_IDENTITY_METRIC_SPACE_REF_PREFIX):
        raise Run9ValidationError(
            f"{field}.identity_metric_space_ref must be a string starting with "
            f"{_IDENTITY_METRIC_SPACE_REF_PREFIX!r} (正本は inputs/identity_metric_space.json への "
            f"参照のみ — 式・値の重複定義禁止), got {ref!r}"
        )
    _resolve_identity_metric_space_ref(
        ref, document=identity_metric_space_document, field=f"{field}.identity_metric_space_ref"
    )
    # PR #322 第4巡指摘 Fix 8（P2, 採用）: 実在走査だけでは「別エントリの
    # 正しい path」を取り違えて指しても通過してしまう（例:
    # reference_render と evaluated_renders の valid path を入れ替え）。
    # エントリ名ごとの期待 path と厳密一致することを追加で要求する。
    expected_ref = _REVISION_BRIDGE_EXPECTED_METRIC_REF[entry_name]
    if ref != expected_ref:
        raise Run9ValidationError(
            f"{field}.identity_metric_space_ref must be exactly {expected_ref!r} for entry "
            f"{entry_name!r} (Fix 8: エントリ→期待 path の厳密対応 — 他エントリの正しい path を "
            f"取り違えて指すことを防ぐ), got {ref!r}"
        )

    new_render_required = entry["new_render_required"]
    if not isinstance(new_render_required, bool) or new_render_required is not requires_new_render:
        raise Run9ValidationError(
            f"{field}.new_render_required must be exactly {requires_new_render!r}, "
            f"got {new_render_required!r}"
        )

    if requires_new_render:
        cell_ref = entry["cell_ref"]
        if cell_ref not in valid_cell_ids:
            raise Run9ValidationError(
                f"{field}.cell_ref {cell_ref!r} does not reference a cell_id declared in probes[]"
            )

    if has_contract_field_ref:
        expected = _REVISION_BRIDGE_CONTRACT_FIELD_REF[entry_name]
        if entry["contract_field_ref"] != expected:
            raise Run9ValidationError(
                f"{field}.contract_field_ref must be exactly {expected!r}, got "
                f"{entry['contract_field_ref']!r}"
            )


def _validate_marker_bearing_str(value: Any, *, field: str, markers: Tuple[str, ...]) -> str:
    text = _require_non_empty_str(value, field=field)
    for marker in markers:
        if marker not in text:
            raise Run9ValidationError(
                f"{field} must contain the marker {marker!r}, got text without that marker"
            )
    return text


def _validate_measurement_boundary(data: Any) -> None:
    if not isinstance(data, dict):
        raise Run9ValidationError(f"measurement_boundary must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _MEASUREMENT_BOUNDARY_KEYS
    if unknown:
        raise Run9ValidationError(f"measurement_boundary has unknown key(s): {sorted(unknown)}")
    missing = _MEASUREMENT_BOUNDARY_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"measurement_boundary missing required key(s): {sorted(missing)}")
    _validate_marker_bearing_str(
        data["scope_statement"], field="measurement_boundary.scope_statement",
        markers=_MEASUREMENT_BOUNDARY_SCOPE_MARKERS,
    )
    _validate_marker_bearing_str(
        data["identity_axis_source"], field="measurement_boundary.identity_axis_source",
        markers=_MEASUREMENT_BOUNDARY_IDENTITY_AXIS_MARKERS,
    )
    _validate_marker_bearing_str(
        data["development_generalization_axis_source"],
        field="measurement_boundary.development_generalization_axis_source",
        markers=_MEASUREMENT_BOUNDARY_DEV_GEN_AXIS_MARKERS,
    )


def _validate_prohibitions(data: Any) -> None:
    if not isinstance(data, list) or not data:
        raise Run9ValidationError(f"prohibitions must be a non-empty list, got {data!r}")
    for i, item in enumerate(data):
        _require_non_empty_str(item, field=f"prohibitions[{i}]")
    joined = "\n".join(data)
    for marker in _PROHIBITION_MARKERS:
        if marker not in joined:
            raise Run9ValidationError(
                f"prohibitions must contain a statement with the marker {marker!r}, got a list "
                "without any such statement"
            )
    for marker in _PROHIBITION_RENDER_INFEASIBLE_CARVEOUT_MARKERS:
        if marker not in joined:
            raise Run9ValidationError(
                f"prohibitions must contain the render-infeasible-cell carve-out marker {marker!r} "
                "(render 不能 cell の是正 repin は水増し禁止の対象外——この区別を明文化する), got a "
                "list without it"
            )


def validate_probe_manifest(data: Mapping[str, Any]) -> None:
    """probe manifest（schema `run9-probe-manifest/1.0`、規約パス
    `PROBE_MANIFEST_PATH` = `evaluation/probe_manifest.json`）の構造を
    検証する。DESIGN_RUN9 §15 Probe Set（P0-P5）の score cells + render
    契約 + revision_bridge（§15 probe 語彙 ↔ identity_metric_space 語彙の
    橋渡し）を凍結した実体 manifest の fail-closed 検証（既存 validator
    群と同じ流儀 — Run9ValidationError・意味論マーカー方式・閉集合）。

    「どう測るか」は本 manifest の対象外（`measurement_boundary` が明文化
    ——identity 軸は `inputs/identity_metric_space.json` が正本のまま、
    P4/P5 の development/generalization 軸の測定仕様は
    `measurement_spec_sha`（別欄、PENDING のまま）が別途凍結する）。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(f"probe manifest must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _PROBE_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"probe manifest has unknown key(s): {sorted(unknown)}")
    missing = _PROBE_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"probe manifest missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_PROBE_MANIFEST:
        raise Run9ValidationError(
            f"probe manifest schema must be exactly {SCHEMA_PROBE_MANIFEST!r}, got {schema!r}"
        )

    _require_non_empty_str(data["note"], field="note")

    probes = data["probes"]
    if not isinstance(probes, list) or len(probes) != len(PROBE_IDS):
        raise Run9ValidationError(
            f"probe manifest.probes must be a list containing exactly the {len(PROBE_IDS)} probes "
            f"{list(PROBE_IDS)} (P0-P5 全6probe必須), got "
            f"{probes if not isinstance(probes, list) else f'{len(probes)} item(s)'}"
        )

    # PR #322 第3巡指摘 Fix 6: renderer の mora 文法（唯一の正本、
    # read-only 参照）を1回だけロードし、全 probe の全 note の kana
    # 検証で使い回す（probe/cell ごとの重複ロードを避ける）。
    phoneme_jp_module = _load_phoneme_jp_module()

    seen_cell_ids: Dict[str, str] = {}
    seen_probe_ids: set = set()
    for i, probe in enumerate(probes):
        if not isinstance(probe, dict):
            raise Run9ValidationError(f"probes[{i}] must be an object, got {type(probe).__name__}")
        probe_id = probe.get("probe_id")
        if probe_id not in PROBE_IDS:
            raise Run9ValidationError(
                f"probes[{i}].probe_id must be one of {list(PROBE_IDS)}, got {probe_id!r}"
            )
        if probe_id in seen_probe_ids:
            raise Run9ValidationError(
                f"probes[{i}].probe_id {probe_id!r} is duplicated — each of P0-P5 must appear "
                "exactly once"
            )
        seen_probe_ids.add(probe_id)
        _validate_probe_object(
            probe, expected_probe_id=probe_id, field=f"probes[{i}]", seen_cell_ids=seen_cell_ids,
            phoneme_jp_module=phoneme_jp_module,
        )
    if seen_probe_ids != set(PROBE_IDS):
        raise Run9ValidationError(
            f"probe manifest.probes is missing required probe_id(s): "
            f"{sorted(set(PROBE_IDS) - seen_probe_ids)}"
        )

    valid_cell_ids = frozenset(seen_cell_ids.keys())

    _validate_render_contract(data["render_contract"])

    revision_bridge = data["revision_bridge"]
    if not isinstance(revision_bridge, dict):
        raise Run9ValidationError(
            f"revision_bridge must be an object, got {type(revision_bridge).__name__}"
        )
    unknown_rb = set(revision_bridge.keys()) - set(_REVISION_BRIDGE_ENTRY_NAMES)
    if unknown_rb:
        raise Run9ValidationError(f"revision_bridge has unknown key(s): {sorted(unknown_rb)}")
    missing_rb = set(_REVISION_BRIDGE_ENTRY_NAMES) - set(revision_bridge.keys())
    if missing_rb:
        raise Run9ValidationError(f"revision_bridge missing required entry(ies): {sorted(missing_rb)}")
    # PR #322 第2巡指摘 Fix 5: dotted path 全体を実文書に対して走査する
    # ため、`inputs/identity_metric_space.json`（凍結・改変禁止の
    # read-only 入力）を1回だけ読み込み、全 revision_bridge エントリで
    # 使い回す（エントリごとの再読み込みを避ける）。
    identity_metric_space_document = _load_identity_metric_space_document()
    for entry_name in _REVISION_BRIDGE_ENTRY_NAMES:
        _validate_revision_bridge_entry(
            revision_bridge[entry_name], entry_name=entry_name,
            field=f"revision_bridge.{entry_name}", valid_cell_ids=valid_cell_ids,
            identity_metric_space_document=identity_metric_space_document,
        )

    _validate_measurement_boundary(data["measurement_boundary"])
    _validate_prohibitions(data["prohibitions"])


# ---------------------------------------------------------------------------
# TRI_CROSSOVER + Run9FounderGenome（DESIGN_RUN9 §9）
# ---------------------------------------------------------------------------

_GENOME_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "voice_id", "ecosystem_role", "ecosystem_generation", "genetic_generation",
    "identity_domain", "coords", "profile_label", "performance_seed", "parents",
    "skill_state", "operator_id", "genome_id",
})


@dataclass(frozen=True)
class Run9FounderGenome:
    """DESIGN_RUN9 §9.2/§9.3 の Founder genome。`genome_id` は
    `_compute_founder_genome_id()` の再計算値以外を持てない（構築時にのみ
    導出され、フィールドとして外部から指定できない）。"""

    voice_id: str
    ecosystem_role: str
    ecosystem_generation: int
    genetic_generation: int
    identity_domain: str
    coords: Run9Coords
    profile_label: str
    performance_seed: int
    parents: Tuple[str, str, str]
    skill_state: str
    operator_id: str
    genome_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "ecosystem_role": self.ecosystem_role,
            "ecosystem_generation": self.ecosystem_generation,
            "genetic_generation": self.genetic_generation,
            "identity_domain": self.identity_domain,
            "coords": self.coords.as_dict(),
            "profile_label": self.profile_label,
            "performance_seed": self.performance_seed,
            "parents": list(self.parents),
            "skill_state": self.skill_state,
            "operator_id": self.operator_id,
            "genome_id": self.genome_id,
        }


# founder_id -> (weights, profile_label) の閉じたテーブル。DESIGN_RUN9 §9.2/9.3
# の凍結重みそのもの。本テーブル自身は非公開（先頭アンダースコア）— 公開
# 経路は `build_founder(domain, founder_id)` のみで、weights を外部から
# 注入する公開 API は存在しない（§27 item 22「no post-listening coordinate
# adjustment API」）。
_FOUNDER_TABLE: Dict[str, Tuple[Tuple[float, float, float], str]] = {
    "R9F-01": (R9F01_WEIGHTS, "AF0_DOMINANT"),
    "R9F-02": (R9F02_WEIGHTS, "USER_DOMINANT"),
}


def _canonicalize_for_hash(obj: Any) -> Any:
    """genome_id 計算用の正規化: float は小数6桁固定表記の文字列へ変換する
    （`voice_genesis/evolution/models.py` `_canonicalize_for_hash` と同一の
    規約 — 0.500000 と 0.5 が異なるバイト列になるのを防ぐ）。"""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise Run9ValidationError(f"non-finite value rejected in genome_id payload: {obj!r}")
        return format(normalize_signed_zero(round(obj, RUN9_COORDINATE_PRECISION)), ".6f")
    if isinstance(obj, str):
        return obj
    if obj is None:
        return None
    if isinstance(obj, (list, tuple)):
        return [_canonicalize_for_hash(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _canonicalize_for_hash(v) for k, v in obj.items()}
    raise Run9ValidationError(f"unsupported type in genome_id payload: {type(obj).__name__}")


def _compute_founder_genome_id(
    *,
    voice_id: str,
    identity_domain: Run9IdentityDomain,
    coords: Run9Coords,
    profile_label: str,
    performance_seed: int,
    parents: Tuple[str, str, str],
    skill_state: str,
    operator_id: str,
) -> str:
    """genome_id = sha256(正規形JSON)[:16]。ハッシュ入力に domain の内容
    ダイジェスト（`Run9IdentityDomain.content_digest()`）を含めることで、
    anchor 未 pin の domain からは実行のたびに異なる genome_id しか
    出せない構造にする（DESIGN_RUN9 §22 step 3→4 の機械強制。実際には
    `build_founder()` が pin 前の domain を先に拒否するため、この性質は
    二重の防御として機能する）。
    """
    payload = {
        "voice_id": voice_id,
        "ecosystem_role": "FOUNDER_CANDIDATE",
        "ecosystem_generation": 0,
        "genetic_generation": 1,
        "identity_domain": identity_domain.domain_id,
        "identity_domain_content_sha256": identity_domain.content_digest(),
        "coords": coords.as_dict(),
        "profile_label": profile_label,
        "performance_seed": performance_seed,
        "parents": list(parents),
        "skill_state": skill_state,
        "operator_id": operator_id,
    }
    canonical = _canonical_json(_canonicalize_for_hash(payload))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_GENOME_ID_LEN]


def _validate_domain_invariants(domain: Run9IdentityDomain) -> None:
    """`Run9IdentityDomain` の不変条件を全数検証する（Codex bot レビュー
    PR #315 第2巡指摘3採用）: `run9_identity_domain_from_dict()` を経由
    せず `Run9IdentityDomain(...)` を直接インスタンス化した偽 domain
    （dataclass はコンストラクタレベルの検証を持たない）が `is_pinned()`
    だけを満たして `build_founder()` へ渡された場合に、domain_id 偽装等を
    ここで検出する。違反は Run9ValidationError。
    """
    if domain.schema != SCHEMA_IDENTITY_DOMAIN:
        raise Run9ValidationError(f"domain.schema must be {SCHEMA_IDENTITY_DOMAIN!r}, got {domain.schema!r}")
    if domain.domain_id != RUN9_DOMAIN_ID:
        raise Run9ValidationError(f"domain.domain_id must be {RUN9_DOMAIN_ID!r}, got {domain.domain_id!r}")
    if domain.anchor_order != RUN9_ANCHOR_ORDER:
        raise Run9ValidationError(
            f"domain.anchor_order must be exactly {RUN9_ANCHOR_ORDER!r}, got {domain.anchor_order!r}"
        )
    if domain.excluded_teacher_identities != RUN9_EXCLUDED_TEACHER_IDENTITIES:
        raise Run9ValidationError(
            f"domain.excluded_teacher_identities must be exactly "
            f"{RUN9_EXCLUDED_TEACHER_IDENTITIES!r}, got {domain.excluded_teacher_identities!r}"
        )
    if not _is_strict_int(domain.coordinate_precision) or domain.coordinate_precision != RUN9_COORDINATE_PRECISION:
        raise Run9ValidationError(
            f"domain.coordinate_precision must be the exact int {RUN9_COORDINATE_PRECISION!r} — bool "
            "and float variants are rejected (Codex bot review PR #315 第5巡指摘1), got "
            f"{domain.coordinate_precision!r} ({type(domain.coordinate_precision).__name__})"
        )
    if domain.normalization != RUN9_NORMALIZATION:
        raise Run9ValidationError(
            f"domain.normalization must be {RUN9_NORMALIZATION!r}, got {domain.normalization!r}"
        )
    if set(domain.anchor_hashes.keys()) != set(RUN9_ANCHOR_ORDER):
        raise Run9ValidationError(
            f"domain.anchor_hashes must have exactly keys {set(RUN9_ANCHOR_ORDER)}, "
            f"got {set(domain.anchor_hashes.keys())!r} (this also rejects a smuggled 'pjs' key)"
        )


def _verify_user_anchor_matches_rights_manifest(
    domain: Run9IdentityDomain, rights_manifest: Mapping[str, Any]
) -> None:
    """`rights_manifest`（4層 rights_manifest 生 dict）から
    `extract_user_identity_attestation_projection()` を実行し、その正規形
    sha256 が `domain.anchor_hashes["user"]` と厳密一致することを検証する
    （Codex bot レビュー PR #320 第5巡指摘, P1, 採用, Fix 7）。

    `extract_user_identity_attestation_projection()` 自体が課す2つの
    fail-closed 前提条件（(i) attestation が attested 形態 (ii)
    `usage_grants.run9_identity_anchor == "granted"` — Fix 6）を、本関数を
    経由するあらゆる呼び出し（`build_founder()` 経由）で毎回強制する。
    さらに、projection の hash が pin 値と一致しない場合（stale pin・
    manifest の改変・単純な取り違え等）も拒否する——値の実物照合が
    「テスト時のみ」ではなく「genome_id 構築の実経路」へ昇格したことの
    核心（Fix 7 の直接目的）。
    """
    if not isinstance(rights_manifest, Mapping):
        raise Run9ValidationError(
            f"rights_manifest must be an object (Mapping), got {type(rights_manifest).__name__}"
        )
    projection = extract_user_identity_attestation_projection(rights_manifest)
    projection_sha = _compute_canonical_pin_sha256(projection)
    expected = domain.anchor_hashes.get("user")
    if projection_sha != expected:
        raise Run9ValidationError(
            "build_founder(): the supplied rights_manifest's identity-attestation "
            "projection hash does not match domain.anchor_hashes['user'] — the pinned user "
            "anchor is stale relative to the supplied manifest (or the supplied manifest is "
            f"not the one the domain was pinned against). expected={expected!r} "
            f"computed={projection_sha!r}"
        )


def _tri_crossover(
    *,
    domain: Run9IdentityDomain,
    weights: Tuple[float, float, float],
    voice_id: str,
    profile_label: str,
    performance_seed: int,
    rights_manifest: Mapping[str, Any],
) -> Run9FounderGenome:
    """TRI_CROSSOVER/1.0 純関数（DESIGN_RUN9 §9.1）。run9 domain では anchor
    が基底ベクトルのため child coords = normalize(weights) そのもの。
    random_search なし・乱数不使用（完全決定論）。本関数は先頭アンダー
    スコアで非公開 — 外部から任意 weights を注入できる公開経路は
    `build_founder(domain, founder_id, rights_manifest=...)` のみ（§27
    item 22）。

    `rights_manifest` の消費（Fix 7）は domain 側の既存検証
    （`_validate_domain_invariants()` / `is_pinned()`）の**後**に行う——
    未 pin・偽装 domain を渡した既存の負例群（is_pinned()==False・forged
    domain_id 等）は従来どおり domain 側のエラーメッセージで拒否され、
    rights_manifest の中身には依存しない。genome_id の計算ロジック
    （coords 正規化・`_compute_founder_genome_id()`）は本 Fix で一切
    変更しない——同一入力（domain/weights/voice_id/profile_label/
    performance_seed）に対する genome_id 値は不変。
    """
    _validate_domain_invariants(domain)
    if not domain.is_pinned():
        raise Run9ValidationError(
            "TRI_CROSSOVER requires a pinned Run9IdentityDomain (all 3 anchor_hashes and "
            "metric_space_sha must be real 64hex sha256, not placeholders) — DESIGN_RUN9 §22 "
            "execution order requires the domain (step 3) to be frozen before founder generation "
            "(step 4)"
        )
    _verify_user_anchor_matches_rights_manifest(domain, rights_manifest)
    w_af0, w_ritsu, w_user = weights
    coords = normalize_run9_coords(w_af0, w_ritsu, w_user)
    _validate_run9_coords_value(coords)

    genome_id = _compute_founder_genome_id(
        voice_id=voice_id, identity_domain=domain, coords=coords, profile_label=profile_label,
        performance_seed=performance_seed, parents=("AF0", "RITSU", "USER_DONOR"),
        skill_state="DEFAULT_NEUTRAL", operator_id=OPERATOR_ID,
    )
    return Run9FounderGenome(
        voice_id=voice_id, ecosystem_role="FOUNDER_CANDIDATE", ecosystem_generation=0,
        genetic_generation=1, identity_domain=domain.domain_id, coords=coords,
        profile_label=profile_label, performance_seed=performance_seed,
        parents=("AF0", "RITSU", "USER_DONOR"), skill_state="DEFAULT_NEUTRAL",
        operator_id=OPERATOR_ID, genome_id=genome_id,
    )


def build_founder(
    domain: Run9IdentityDomain, founder_id: str, *, rights_manifest: Mapping[str, Any]
) -> Run9FounderGenome:
    """RUN9 Founder genome を構築する唯一の公開経路。`founder_id` は
    `{"R9F-01", "R9F-02"}` のいずれかのみを受け付け、凍結重みテーブル
    `_FOUNDER_TABLE` から重みを引く。任意の weights を外部から注入する
    公開 API は存在しない（DESIGN_RUN9 §27 item 22 / §9.4）。

    `rights_manifest`（4層 rights_manifest 生 dict、`inputs/
    rights_manifest.json` をロードしたもの）は**デフォルト値のない必須
    keyword-only 引数**（Codex bot レビュー PR #320 第5巡指摘, P1, 採用,
    Fix 7）: `extract_user_identity_attestation_projection(rights_
    manifest)` を実行し、その正規形 sha256 が `domain.anchor_hashes
    ["user"]` と厳密一致することを毎回検証する
    （`_verify_user_anchor_matches_rights_manifest()` 参照）。不一致・
    取消（`usage_grants.run9_identity_anchor` が `"granted"` でない）・
    pending 形態（`attestation.attested` が `True` でない）はいずれも
    `Run9ValidationError` で拒否する。

    **経緯（Fix 6→Fix 7）**: Fix 6 は `extract_user_identity_attestation_
    projection()` へ取消検知のガードを追加したが、当時の呼び出し元は
    テストと docstring のみで、`build_founder()` 自身は
    `domain.anchor_hashes["user"]`（保存済み64hex文字列）のみを消費し
    `rights_manifest.json` を一切参照しなかった——取消済み・stale な
    manifest でも `build_founder()` は成功し続ける非接続状態だった
    （第4巡回帰テスト `test_fix320_6_gate_is_projection_extraction_not_
    build_founder_or_gate_state` が当時「これが期待どおりの挙動」として
    明文化していたが、この記述は本 Fix 7 で撤回・是正した——実効性の
    無いガードだったため）。Fix 7 は `rights_manifest` を必須引数化する
    ことで、genome_id 構築の実経路（この関数）自体に検証を配線した。

    **非対称の設計理由**（af0/ritsu/metric_space_sha との比較）:
    af0/ritsu/metric_space_sha の pin は取消意味論を持たない外部
    アーティファクト（波音リツ配布 zip・AF-P0 canonical manifest・
    identity metric space 仕様）への**形状**pin であり、その内容が
    「後から取消される」という遷移軸自体が存在しない——内容の実物照合は
    引き続き R9-G1 tooling（machine-dependent、未実装）の職務のまま
    変更しない。user anchor だけが「in-repo の可変文書
    （`inputs/rights_manifest.json`）+ User が事後に取消し得る許諾
    （`usage_grants.run9_identity_anchor`）」を源泉とするため、唯一
    この anchor だけが「pin 後に取消され得る」という性質を持つ——本
    Fix はこの非対称性そのものに対応するものであり、`gate_state()`/
    `Run9IdentityDomain.is_pinned()` を含む他の gate 判定はいずれも
    構造述語のまま変更しない（repo 全体の「gate=構造述語、実体照合=
    R9-G1 tooling」原則は af0/ritsu/metric_space_sha 側で不変）。
    """
    if founder_id not in _FOUNDER_TABLE:
        raise Run9ValidationError(
            f"founder_id must be one of {sorted(_FOUNDER_TABLE)}, got {founder_id!r}"
        )
    weights, profile_label = _FOUNDER_TABLE[founder_id]
    return _tri_crossover(
        domain=domain, weights=weights, voice_id=founder_id, profile_label=profile_label,
        performance_seed=SHARED_PERFORMANCE_SEED, rights_manifest=rights_manifest,
    )


def founder_genome_from_dict(
    data: Any, *, domain: Run9IdentityDomain, rights_manifest: Mapping[str, Any]
) -> Run9FounderGenome:
    """JSON dict から Run9FounderGenome を再構築する。fail-closed（未知
    キー拒否）+ 構造検証の後、`build_founder(domain, voice_id,
    rights_manifest=rights_manifest)` で正典を再構築し `to_dict()`
    （genome_id 含む）が完全一致することを要求する
    （改ざん検出。Codex bot レビュー PR #315 指摘3採用: 従来は voice_id /
    coords / genome_id 相互の整合を検証しておらず、「R9F-01 ラベル +
    R9F-02 座標 + 任意の16hex genome_id」のような偽装 genome document が
    構造検証だけを通過し得た）。"""
    if not isinstance(data, dict):
        raise Run9ValidationError(f"genome document must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _GENOME_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"genome document has unknown key(s): {sorted(unknown)}")
    missing = _GENOME_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"genome document missing required key(s): {sorted(missing)}")

    voice_id = data["voice_id"]
    if not isinstance(voice_id, str):
        raise Run9ValidationError(f"voice_id must be a string, got {voice_id!r}")
    if not _FOUNDER_ID_RE.match(voice_id):
        raise Run9ValidationError(f"voice_id must match {_FOUNDER_ID_RE.pattern}, got {voice_id!r}")

    ecosystem_role = data["ecosystem_role"]
    if not isinstance(ecosystem_role, str) or ecosystem_role != "FOUNDER_CANDIDATE":
        raise Run9ValidationError(f"ecosystem_role must be 'FOUNDER_CANDIDATE', got {ecosystem_role!r}")
    # ecosystem_generation/genetic_generation/performance_seed: 厳密int等値
    # （Codex bot レビュー PR #315 第6巡指摘2採用）。`!= 0`/`!= 1` のような
    # 素の等価比較は bool（`False == 0`/`True == 1`）や float
    # （`0.0 == 0`/`909001.0 == 909001`）を黙って通してしまう — 通過を
    # 許すと `_canonicalize_for_hash()` の genome_id 直列化で非正準値が
    # 混入しうる。`_is_strict_int()` で bool/float を先に排除する。
    ecosystem_generation = data["ecosystem_generation"]
    if not _is_strict_int(ecosystem_generation) or ecosystem_generation != 0:
        raise Run9ValidationError(f"ecosystem_generation must be the exact int 0, got {ecosystem_generation!r}")
    genetic_generation = data["genetic_generation"]
    if not _is_strict_int(genetic_generation) or genetic_generation != 1:
        raise Run9ValidationError(f"genetic_generation must be the exact int 1, got {genetic_generation!r}")
    identity_domain = data["identity_domain"]
    if not isinstance(identity_domain, str) or identity_domain != RUN9_DOMAIN_ID:
        raise Run9ValidationError(f"identity_domain must be {RUN9_DOMAIN_ID!r}, got {identity_domain!r}")

    coords_raw = data["coords"]
    _reject_pjs_key(context="coords", keys=coords_raw if isinstance(coords_raw, dict) else {})
    if not isinstance(coords_raw, dict) or set(coords_raw.keys()) != set(RUN9_ANCHOR_ORDER):
        raise Run9ValidationError(f"coords must have exactly keys {list(RUN9_ANCHOR_ORDER)}, got {coords_raw!r}")
    # Codex bot レビュー PR #315 第5巡指摘2採用: 生値を `float(...)` へ黙って
    # 型強制するのではなく `_require_valid_coord_scalar()` で「bool でない
    # int/有限float」であることを検証してから変換する。改ざん検出を掲げる
    # 本関数が文字列（例 "0.6"）等の非正準値まで黙って正規化して受理すると、
    # 非正準・改変された genome document が builder 照合を通過して正典
    # として返る契約矛盾になる。
    coords = Run9Coords(**{
        k: _require_valid_coord_scalar(coords_raw[k], f"coords.{k}") for k in RUN9_ANCHOR_ORDER
    })
    _validate_run9_coords_value(coords)

    profile_label = data["profile_label"]
    if not isinstance(profile_label, str) or profile_label not in ("AF0_DOMINANT", "USER_DOMINANT"):
        raise Run9ValidationError(f"profile_label invalid: {profile_label!r}")
    performance_seed = data["performance_seed"]
    if not _is_strict_int(performance_seed) or performance_seed != SHARED_PERFORMANCE_SEED:
        raise Run9ValidationError(
            f"performance_seed must be the exact int {SHARED_PERFORMANCE_SEED!r}, got {performance_seed!r}"
        )
    parents_raw = data["parents"]
    # isinstance(list) を先行させる（Codex bot レビュー PR #315 第6巡指摘2
    # 採用）: `list(parents_raw) != [...]` は `parents_raw` が
    # `{"AF0": 1, "RITSU": 1, "USER_DONOR": 1}` のような dict でも
    # `list(dict)` がキー列挙で `["AF0","RITSU","USER_DONOR"]` を返し
    # 一致してしまう（`excluded_teacher_identities` の同型欠陥と同じ穴）。
    if not isinstance(parents_raw, list) or parents_raw != ["AF0", "RITSU", "USER_DONOR"]:
        raise Run9ValidationError(f"parents must be exactly ['AF0','RITSU','USER_DONOR'], got {parents_raw!r}")
    skill_state = data["skill_state"]
    if not isinstance(skill_state, str) or skill_state != "DEFAULT_NEUTRAL":
        raise Run9ValidationError(f"skill_state must be 'DEFAULT_NEUTRAL', got {skill_state!r}")
    operator_id = data["operator_id"]
    if not isinstance(operator_id, str) or operator_id != OPERATOR_ID:
        raise Run9ValidationError(f"operator_id must be {OPERATOR_ID!r}, got {operator_id!r}")

    genome_id = data["genome_id"]
    if not isinstance(genome_id, str) or not _GENOME_ID_RE.match(genome_id):
        raise Run9ValidationError(
            f"genome_id must be exactly {_GENOME_ID_LEN} lowercase hex characters, got {genome_id!r}"
        )

    declared = Run9FounderGenome(
        voice_id=voice_id, ecosystem_role="FOUNDER_CANDIDATE", ecosystem_generation=0,
        genetic_generation=1, identity_domain=RUN9_DOMAIN_ID, coords=coords,
        profile_label=data["profile_label"], performance_seed=data["performance_seed"],
        parents=("AF0", "RITSU", "USER_DONOR"), skill_state="DEFAULT_NEUTRAL",
        operator_id=OPERATOR_ID, genome_id=genome_id,
    )

    # builder 照合（改ざん検出）: voice_id から凍結重みで正典を再構築し、
    # 宣言値と完全一致することを要求する。voice_id/coords が食い違えば
    # coords 不一致で、genome_id だけが差し替えられていれば genome_id
    # 不一致で検出される。
    canonical = build_founder(domain, voice_id, rights_manifest=rights_manifest)
    if declared.to_dict() != canonical.to_dict():
        raise Run9ValidationError(
            "genome document does not match the canonical reconstruction from "
            f"build_founder(domain, {voice_id!r}, rights_manifest=...) — "
            f"declared={declared.to_dict()!r} canonical={canonical.to_dict()!r} "
            "(tampering or corruption)"
        )
    return canonical


def issue_founder_genome_document(
    founder_id: str, *, domain: Run9IdentityDomain, rights_manifest: Mapping[str, Any]
) -> bytes:
    """RUN9-BIRTH-PREP-1 §A: Founder genome の永続文書発行を行う唯一の公開
    経路。内部は必ず `build_founder(domain, founder_id, rights_manifest=
    rights_manifest)` を経由するため、Fix 6/7 の fail-closed ガード
    （attested 前提条件・anchor grant 検証・user anchor 実物照合）が発行の
    たびに毎回実行される——本関数は `build_founder()` を迂回した genome 構築
    経路を一切持たない。

    直列化はここで凍結する:
    `(json.dumps(genome.to_dict(), ensure_ascii=False, indent=2,
    sort_keys=True) + "\\n").encode("utf-8")`。

    発行 = この関数の出力バイト列をそのまま `founders/R9F-0x_genome.json`
    として書き出すこと。手書き・別形式（インデント幅の違い・末尾改行の
    有無・キー順の違いを含む）は不正な発行であり、`RUN9_CONTRACT.yaml`
    `founder_genome_shas` が pin するのはこの関数の出力バイトの sha256
    （`compute_file_sha256()` と同じファイル実バイト規約）に限る。
    """
    genome = build_founder(domain, founder_id, rights_manifest=rights_manifest)
    return (json.dumps(genome.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


# rev RUN9-BIRTH-PREP-1: 永続 genome 文書の規約配置（`PRACTICE_MANIFEST_PATH`
# と同じ命名規約 — schema から機械的に導出せず、リポジトリ内の固定配置として
# 凍結する）。
FOUNDER_GENOME_DIR = _THIS_DIR / "founders"


def founder_genome_document_path(founder_id: str) -> Path:
    """`founders/R9F-0x_genome.json` の規約パスを返す。"""
    if not _FOUNDER_ID_RE.match(founder_id):
        raise Run9ValidationError(
            f"founder_id must match {_FOUNDER_ID_RE.pattern}, got {founder_id!r}"
        )
    return FOUNDER_GENOME_DIR / f"{founder_id}_genome.json"


# ---------------------------------------------------------------------------
# Run Contract（DESIGN_RUN9 §23 `voicegenesis-run-contract/1.0`）
# ---------------------------------------------------------------------------

_PIN_STATUSES: Tuple[str, str, str] = ("PINNED", "PENDING", "BLOCKED")
_PIN_FIELD_ALLOWED_KEYS: FrozenSet[str] = frozenset({"value", "status", "reason", "source"})
_PIN_FIELD_REQUIRED_KEYS: FrozenSet[str] = frozenset({"value", "status"})

# §23 の yaml に列挙された全 pin 欄（design_doc_sha256 は §23 に無いが
# タスク指示により本 contract 実装で追加する欄 — 編入した設計書ファイルの
# 実 sha256 を PINNED で記録する）。
CONTRACT_PIN_FIELDS: Tuple[str, ...] = (
    "design_doc_sha256",
    # design_revision 0.2 で追加（User 裁定 2026-08-24）: 現行 design_revision
    # の差分メモ自体の実 sha256（design_doc_sha256 と同じ前例方式）。0.2 →
    # 0.3 進行時は値のみ更新し欄自体は再利用する（DESIGN_RUN9_REVISION_0.3.md
    # 「design_revision 系譜」参照）。
    "design_revision_doc_sha256",
    # design_revision 0.3 で追加（User 裁定 2026-08-24, PoR メモ編入）:
    # POR_CONCEPT_ADJUDICATION_20260824.txt 自体の実 sha256（同じ前例方式）。
    "por_adjudication_sha256",
    "attempt_id",
    "repository_commit_sha",
    "dataset_manifest_sha",
    "dataset_row_order_sha",
    "config_sha",
    "dependency_pins_sha",
    "execution_profile_sha",
    "seed_policy_sha",
    "expected_speaker_map_sha",
    "backbone_checkpoint_sha",
    # design_revision 0.2 で追加: inputs/backbone_runtime_bundle.json 自体の
    # 実 sha256。bundle 内に PENDING 欄が残る場合はこの欄も PENDING とする
    # （bundle 内 PENDING 解消後に pin — CONTRACT_PIN_FIELDS のコメント規約
    # どおり loader 自体は bundle の中身までは検査しない。整合は手動運用）。
    "backbone_runtime_bundle_sha",
    # rev 0.3 で `lesson_sha` から改名（User 外部レビュー PR #317 P1-2
    # 採用）: 「lesson」という曖昧な名称のまま EDUCATION 専用欄であることを
    # schema レベルでも固定する。値の性質・PENDING 理由は不変
    # （`validate_education_lesson_manifest()` が中身の最低要件を検証する）。
    "education_technique_lesson_manifest_sha",
    # rev 0.3 で `practice_split_sha` から改名（User 外部レビュー PR #317
    # P1-2 採用）: PRACTICE_FROM_AUDIO 枝の train/validation/sealed-holdout
    # split manifest の sha256。`education_technique_lesson_manifest_sha`
    # と対になる pre-run 必須欄 — 稽古でも train/holdout 分離が必須
    # （PoR §12: sealed holdout は学習中使用禁止）であり、この分離が
    # 学習開始前に凍結・封印されていることを他の pre-run 欄と同列に
    # PINNED で証拠づける（`validate_practice_split_manifest()` が中身の
    # 最低要件を検証する）。
    "practice_audio_split_manifest_sha",
    # rev 0.3 新設（User 外部レビュー PR #317 P1-1 採用）: 枝別書込境界
    # policy manifest（`inputs/branch_write_policy.json`）自体の実
    # sha256。本 PR でファイル内容を確定するため PINNED（本欄自体は
    # design_doc_sha256 と同じファイル実バイト規約 — `compute_file_sha256`
    # 参照）。
    "branch_write_policy_sha",
    "learning_recipe_sha",
    "probe_manifest_sha",
    "measurement_spec_sha",
    "hypothesis_algebra_sha",
    "human_evaluation_protocol_sha",
    "artifact_manifest_sha",
    "cost_record_sha",
    "failure_abort_criteria_sha",
)

# founder_genome_shas は {R9F-01: pin_field, R9F-02: pin_field} という
# 入れ子構造のため別枠で扱う（CONTRACT_PIN_FIELDS には含めない）。
CONTRACT_FOUNDER_IDS: Tuple[str, str] = ("R9F-01", "R9F-02")

# post-run pin（実行後にのみ実測できる証拠欄）。gate_state() の READY 判定
# から除外する（DESIGN_RUN9 §27 item 49「incomplete Hard Gate set -> BLOCKED」
# の pre-run 側判定 — artifact/cost は run record closure 側の要件）。
CONTRACT_POST_RUN_PIN_FIELDS: FrozenSet[str] = frozenset({"artifact_manifest_sha", "cost_record_sha"})

# optional pin（post-run とは別の第3分類、PR #317 Codex bot レビュー第1巡
# Fix 1 採用）: rev 0.3 改訂F により人間知覚 Gate は必須ではなくなった
# （DESIGN_RUN9_REVISION_0.3.md 改訂F — 機械評価 + claim ceiling 明記へ
# 変更し、人間知覚評価は後続 Run へ送る。v0.1 §28 Human Audit も optional
# 化）。`human_evaluation_protocol_sha` は advisory な blind human audit を
# 実施する場合にのみ pin する欄であり、PENDING のままでも gate_state() の
# READY 判定を妨げない — post-run 欄（実行後にしか実測できない）とは理由が
# 異なるため別の frozenset として区別する（post-run は「まだ実測できない」、
# optional は「実施しないなら永久に PENDING のままで構わない」）。
CONTRACT_OPTIONAL_PIN_FIELDS: FrozenSet[str] = frozenset({"human_evaluation_protocol_sha"})

_CONTRACT_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset(
    {
        "schema", "run_id", "experiment_id", "design_revision", "design_doc",
        "interventions", "baseline_run", "parent_designs",
        "founder_genome_shas", "claim_strength_target",
        # rev 0.3 新設（User 外部レビュー PR #317 P2-2 採用）: pin 欄では
        # なく通常欄。`HUMAN_AUDIT_MODES` のいずれかの文字列値のみ許容。
        "human_audit_mode",
        # rev 0.4 新設（DESIGN_RUN9_REVISION_0.4.md、2026-08-25 User 追加
        # 裁定「確認メモ / RUN9 用語整理」指示2）: pin 欄ではなく通常欄。
        # `validate_performance_source_block()` が構造を検証する。
        "performance_source",
    }
    | set(CONTRACT_PIN_FIELDS)
)

_RESERVED_CONTRACT_SUBSTRINGS: Tuple[str, ...] = ("total_score", "totalscore")


def _reject_total_score_vocabulary(*, context: str, names: Any) -> None:
    """DESIGN_RUN9 §27 item 40「no TotalScore field in evaluation/result
    schema」: contract / genome のどのフィールド名にも total_score 系の
    語彙を許さない（大文字小文字非依存）。"""
    for name in names:
        lowered = str(name).replace("_", "").lower()
        for forbidden in _RESERVED_CONTRACT_SUBSTRINGS:
            if forbidden.replace("_", "") in lowered:
                raise Run9ValidationError(
                    f"{context} field name {name!r} contains reserved total-score vocabulary "
                    f"({forbidden!r}) — DESIGN_RUN9 §27 item 40 permanently forbids a single "
                    "aggregate score field"
                )


def _validate_pin_field_value_shape(name: str, value: Any) -> None:
    """PINNED 状態の pin 欄 value の欄名別整形式検証（Codex bot レビュー
    PR #315 指摘1採用）: `founder_genome_shas.R9F-0x` は **64hex sha256**
    形式（PR #315 第7巡指摘1採用 — 意味論の是正: §23 は本欄を `_sha`
    ではなく `founder_genome_shas` と命名しているが値の性質は他の `_sha`
    欄と同じ「永続 artifact のバイト sha256」であり、R9-G12
    「Genome bytes の replay 照合」が要求するのは `founders/R9F-0x_genome.json`
    という**永続 genome 文書ファイルのバイト列**の sha256 である。第1巡
    修正が採用した16hex `genome_id`（`compute_genome_id()` が返す正規形
    JSON 由来の**内容 ID**）は、genome 文書内部の1フィールドとして保持
    される値であって、文書ファイル自体のバイト凍結ではない — 同じ
    genome_id を宣言したまま notes 欄や整形（インデント等）だけ変えた
    再直列化ファイルを検出できない意味論の誤りだった）、`attempt_id` は
    正の文法 `_ATTEMPT_ID_RE` に完全一致（PR #315 第4巡指摘採用: 旧実装は
    「非空 + プレースホルダ正規表現不一致」というブラックリスト式で、
    `" <PIN_BEFORE_RUN> "`（前後空白で `strip()` 後だけ比較していたため
    素通り）や `<PIN_1>`（大文字+アンダースコア限定のブラックリスト
    正規表現の想定外）のようなプレースホルダ変種を追撃しきれなかった —
    個別変種のブラックリスト追撃ではなく、先頭英数字・以降英数字/`.`/
    `_`/`-` のみという正の文法で「`<`/`>`/空白を構造的に許容しない」形に
    終端する）、`repository_commit_sha` は git commit object ID の 40hex
    （SHA-1）形式（PR #315 第3巡指摘1: 64hex を要求すると正直な git sha
    を PINNED にしても contract が構造的に READY へ到達不能だった — 第1巡
    修正の不備）、それ以外の `_sha`/`_sha256` で終わるトップレベル欄
    （`design_doc_sha256` を含む）は 64hex sha256 形式を要求する。
    """
    if name.startswith("founder_genome_shas."):
        if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
            raise Run9ValidationError(
                f"{name}.value must be exactly 64 lowercase hex characters (sha256 of the persisted "
                "genome document file, e.g. founders/R9F-0x_genome.json — NOT the 16hex genome_id "
                "content-id, which is a field inside that document rather than a byte-freeze of the "
                f"document itself) when status is PINNED, got {value!r}"
            )
        return
    if name == "attempt_id":
        if not isinstance(value, str) or not _ATTEMPT_ID_RE.match(value):
            raise Run9ValidationError(
                f"{name}.value must match {_ATTEMPT_ID_RE.pattern!r} when status is PINNED (leading "
                "alphanumeric, then alphanumeric/'.'/'_'/'-' only — this structurally excludes "
                "whitespace and '<'/'>' placeholder markers rather than blacklisting individual "
                f"placeholder variants), got {value!r}"
            )
        return
    if name == "repository_commit_sha":
        if not isinstance(value, str) or not _SHA1_HEX_RE.match(value):
            raise Run9ValidationError(
                f"{name}.value must be exactly 40 lowercase hex characters (git SHA-1 object ID "
                f"format — this repository uses SHA-1 commit ids, not sha256) when status is PINNED, "
                f"got {value!r}"
            )
        return
    if name.endswith("_sha") or name.endswith("_sha256"):
        if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
            raise Run9ValidationError(
                f"{name}.value must be exactly 64 lowercase hex characters (sha256 format) when "
                f"status is PINNED, got {value!r}"
            )
        return


def _validate_pin_field(name: str, field: Any) -> Dict[str, Any]:
    if not isinstance(field, dict):
        raise Run9ValidationError(f"{name} must be an object, got {type(field).__name__}")
    unknown = set(field.keys()) - _PIN_FIELD_ALLOWED_KEYS
    if unknown:
        raise Run9ValidationError(f"{name} has unknown key(s): {sorted(unknown)}")
    missing = _PIN_FIELD_REQUIRED_KEYS - set(field.keys())
    if missing:
        raise Run9ValidationError(f"{name} missing required key(s): {sorted(missing)}")
    status = field["status"]
    if status not in _PIN_STATUSES:
        raise Run9ValidationError(f"{name}.status must be one of {_PIN_STATUSES}, got {status!r}")
    if "reason" in field and not isinstance(field["reason"], str):
        raise Run9ValidationError(f"{name}.reason must be a string, got {field['reason']!r}")
    if "source" in field and field["source"] is not None and not isinstance(field["source"], str):
        raise Run9ValidationError(f"{name}.source must be a string or null, got {field['source']!r}")
    if status == "PINNED":
        # PENDING/BLOCKED は従来どおり value が null でもよい（正直な未 pin
        # 表現）。PINNED を名乗る欄だけは value 非 null + 欄名別整形式を
        # load 時に強制する — 「全欄 status だけ PINNED にして READY を
        # 騙る」経路を loader 段で閉じる（Codex bot レビュー PR #315 指摘1）。
        value = field["value"]
        if value is None:
            raise Run9ValidationError(
                f"{name}.status is PINNED but value is null — a PINNED pin field must carry a real "
                "value (Codex bot review PR #315 指摘1)"
            )
        _validate_pin_field_value_shape(name, value)
    return dict(field)


@dataclass(frozen=True)
class Run9RunContract:
    """DESIGN_RUN9 §23 の `voicegenesis-run-contract/1.0`。value は検証済み
    生 dict をそのまま保持する（RUN9_CONTRACT.yaml 全体の忠実な表現。
    フィールド意味の解釈は `gate_state()` 等の別関数が担う）。"""

    raw: Dict[str, Any]

    def pin_field(self, name: str) -> Dict[str, Any]:
        return self.raw[name]

    def founder_genome_sha(self, founder_id: str) -> Dict[str, Any]:
        return self.raw["founder_genome_shas"][founder_id]

    def intervention_take_count_field(self, name: str) -> Dict[str, Any]:
        """`interventions` 配下の入れ子 pin 欄（`INTERVENTION_TAKE_COUNT_FIELDS`
        の各要素）を返す。`pin_field()`/`founder_genome_sha()` と同じ
        アクセサ規約（Codex bot レビュー PR #318 第8巡 Fix 22 で新設 —
        `gate_state()` がこの入れ子 pin も pre-run 判定に含めるための足場）。"""
        return self.raw["interventions"][name]


def _is_field_pinned(field: Mapping[str, Any]) -> bool:
    return field.get("status") == "PINNED"


def load_run9_contract(data: Mapping[str, Any]) -> Run9RunContract:
    """RUN9_CONTRACT.yaml をパース済み dict として受け取り検証する。
    fail-closed（未知キー拒否）+ run_id は厳密に "RUN9"（"RUN9A"等は拒否 —
    DESIGN_RUN9 §27 item 54）+ total_score 語彙拒否（item 40）。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(f"contract document must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _CONTRACT_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"contract document has unknown key(s): {sorted(unknown)}")
    missing = _CONTRACT_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"contract document missing required key(s): {sorted(missing)}")

    _reject_total_score_vocabulary(context="contract", names=data.keys())

    schema = data["schema"]
    if schema != SCHEMA_RUN_CONTRACT:
        raise Run9ValidationError(f"schema must be {SCHEMA_RUN_CONTRACT!r}, got {schema!r}")

    run_id = data["run_id"]
    if not isinstance(run_id, str) or run_id != RUN_ID:
        raise Run9ValidationError(
            f"run_id must be exactly {RUN_ID!r} — branch numbers (e.g. 'RUN9A'/'RUN9B'/'RUN9C') are "
            f"forbidden (DESIGN_RUN9 §27 item 54 / header note: design changes are tracked via "
            f"design_revision, execution history via attempt_id), got {run_id!r}"
        )

    experiment_id = data["experiment_id"]
    if not isinstance(experiment_id, str) or experiment_id != EXPERIMENT_ID:
        raise Run9ValidationError(f"experiment_id must be {EXPERIMENT_ID!r}, got {experiment_id!r}")

    design_revision = data["design_revision"]
    if not isinstance(design_revision, str) or design_revision != DESIGN_REVISION:
        # PR #317 Codex bot レビュー第1巡 Fix 2 採用: 診断メッセージに
        # 固定ファイル名（例: "DESIGN_RUN9_REVISION_0.2.md"）をハード
        # コードしていると、design_revision を上げるたびにメッセージ内の
        # ファイル名だけが陳腐化する（実際に 0.2 -> 0.3 進行時に発生した）。
        # `DESIGN_REVISION` 定数から f-string でファイル名を導出すること
        # で、以後の revision bump でこの診断が再び古びない構造にする。
        raise Run9ValidationError(
            f"design_revision must be exactly {DESIGN_REVISION!r} (current revision — "
            f"DESIGN_RUN9_REVISION_{DESIGN_REVISION}.md). A contract declaring an older "
            "revision (e.g. '0.1', '0.2') is rejected by design: revising the design requires "
            "bumping design_revision and keeping the old attempt as append-only history, got "
            f"{design_revision!r}"
        )

    if not isinstance(data["design_doc"], str) or not data["design_doc"]:
        raise Run9ValidationError(f"design_doc must be a non-empty string, got {data['design_doc']!r}")

    # rev 0.3（改訂A、PoR §1/§3/§4）: 旧 `single_intervention`
    # （description + 単一 `changed_edge`）は `interventions`
    # （description + `edges`[2] + `control_branch`）へ改訂された。
    # `_CONTRACT_TOP_LEVEL_KEYS` が `single_intervention` を許容しないため、
    # 旧形式の contract は「未知キー(single_intervention) + 欠落キー
    # (interventions)」の時点で fail-closed 拒否される（この関数の冒頭、
    # unknown/missing チェックで既に落ちている）。
    interventions = data["interventions"]
    if not isinstance(interventions, dict):
        raise Run9ValidationError("interventions must be an object")
    allowed_interv_keys = {"description", "edges", "control_branch"} | set(
        INTERVENTION_TAKE_COUNT_FIELDS
    )
    unknown_interv = set(interventions.keys()) - allowed_interv_keys
    if unknown_interv:
        raise Run9ValidationError(f"interventions has unknown key(s): {sorted(unknown_interv)}")
    missing_interv = allowed_interv_keys - set(interventions.keys())
    if missing_interv:
        raise Run9ValidationError(f"interventions missing key(s): {sorted(missing_interv)}")
    interv_description = interventions["description"]
    if not isinstance(interv_description, str) or not interv_description.strip():
        raise Run9ValidationError(
            f"interventions.description must be a non-empty string, got {interv_description!r}"
        )
    interv_edges = interventions["edges"]
    # parent_designs と同型の正典（`INTERVENTION_EDGES`）への順序込み厳密
    # 一致（Codex bot レビュー PR #315 第7巡指摘2 と同種の終端規律を
    # 新設フィールドへ最初から適用する）。
    if not isinstance(interv_edges, list) or tuple(interv_edges) != INTERVENTION_EDGES:
        raise Run9ValidationError(
            f"interventions.edges must be exactly {list(INTERVENTION_EDGES)} (order included) — "
            "PoR §3/§4 は稽古(PRACTICE_FROM_AUDIO)と教育(TRANSFER_TECHNIQUE)を別 Edge として固定する"
            f"（他のエッジへの差し替えは design_revision を上げた別 attempt として扱う）, got "
            f"{interv_edges!r}"
        )
    interv_control_branch = interventions["control_branch"]
    if interv_control_branch != CONTROL_BRANCH:
        raise Run9ValidationError(
            f"interventions.control_branch must be exactly {CONTROL_BRANCH!r} — PoR §4 の無介入"
            f"replay 枝は固定名, got {interv_control_branch!r}"
        )

    # Codex bot レビュー PR #318 第7巡 Fix 20 採用（P1）: C0/C1 校正標本の
    # per-founder テイク数 pin 欄。他の pin 欄と同型の {value, status,
    # reason?, source?} 形（`_validate_pin_field()`）で包絡を検証し、
    # PINNED 昇格時は `_require_positive_int()`（bool/float/0/負値を拒否
    # する厳密正 int 判定）で値自体の型を検証する。`_validate_pin_field()`
    # は欄名が `_sha`/`_sha256` サフィックスでも `attempt_id`/
    # `founder_genome_shas.*` でもない場合は値の形式まで検査しないため
    # （PIN_FIELD_ALLOWED_KEYS の envelope 検査のみ）、テイク数の型検証は
    # ここで明示的に追加する。
    for take_field_name in INTERVENTION_TAKE_COUNT_FIELDS:
        take_field = _validate_pin_field(
            f"interventions.{take_field_name}", interventions[take_field_name]
        )
        if _is_field_pinned(take_field):
            _require_positive_int(
                take_field["value"], field=f"interventions.{take_field_name}.value"
            )

    if data["baseline_run"] is not None:
        raise Run9ValidationError(f"baseline_run must be null (RUN9 has no baseline_run), got {data['baseline_run']!r}")

    parent_designs = data["parent_designs"]
    # 全要素が非空 str の非空 list であることを厳密化する（Codex bot レビュー
    # PR #315 第6巡指摘1採用: RUN9_CONTRACT.yaml 側の erratum 是正 — 設計書
    # §6 は5件の parent_designs を宣言するが §23/旧 contract は3件だった。
    # 完全側の §6 へ拡張したため、要素の型・非空も併せて厳密化する）。
    if not isinstance(parent_designs, list) or not parent_designs:
        raise Run9ValidationError("parent_designs must be a non-empty list")
    for i, item in enumerate(parent_designs):
        if not isinstance(item, str) or not item.strip():
            raise Run9ValidationError(f"parent_designs[{i}] must be a non-empty string, got {item!r}")
    # 正典（`PARENT_DESIGNS`）との順序込み厳密一致を強制する（Codex bot
    # レビュー PR #315 第7巡指摘2採用）: 第6巡修正は型・非空のみを検査して
    # おり、`['unrelated']` のような無関係な5件や、正しい5件の順序入れ替え・
    # 一部欠落は素通りしていた。DESIGN_RUN9 §6 を正とする凍結リストへの
    # 完全一致（要素・順序とも）で終端する。
    if tuple(parent_designs) != PARENT_DESIGNS:
        raise Run9ValidationError(
            f"parent_designs must be exactly {list(PARENT_DESIGNS)} (order included) — DESIGN_RUN9 "
            "§6 is the canonical dependency declaration (§23's 3-item Run Contract template is a "
            f"documented erratum; §6 governs), got {parent_designs!r}"
        )

    for name in CONTRACT_PIN_FIELDS:
        _validate_pin_field(name, data[name])

    founder_shas = data["founder_genome_shas"]
    if not isinstance(founder_shas, dict) or set(founder_shas.keys()) != set(CONTRACT_FOUNDER_IDS):
        raise Run9ValidationError(
            f"founder_genome_shas must have exactly keys {list(CONTRACT_FOUNDER_IDS)}, got {founder_shas!r}"
        )
    for founder_id in CONTRACT_FOUNDER_IDS:
        _validate_pin_field(f"founder_genome_shas.{founder_id}", founder_shas[founder_id])

    # 両 founder が PINNED のとき、value（genome_id）の相異を強制する
    # （Codex bot レビュー PR #315 第3巡指摘2採用）: 同一 genome_id は二体の
    # dual-founder 比較の前提そのものが崩れる（R9F-01/R9F-02 は異なる座標
    # から生成される別 Genome のはずで、genome_id が一致するのは改ざんか
    # コピペ誤りしかあり得ない）。片方以下が PINNED の場合は判定しない
    # （PENDING 同士・片方だけ PINNED の状態は正直な未 pin 表現として許容）。
    # 正典 founder 記録との整合（宣言 genome_id が実際に
    # build_founder(domain, founder_id) の再計算値と一致するか）は、domain
    # が必要なため contract load の責務にせず `founder_genome_from_dict()`
    # の builder 照合が担う（役割分担）。
    if all(_is_field_pinned(founder_shas[fid]) for fid in CONTRACT_FOUNDER_IDS):
        values = {fid: founder_shas[fid]["value"] for fid in CONTRACT_FOUNDER_IDS}
        if len(set(values.values())) != len(values):
            raise Run9ValidationError(
                f"founder_genome_shas values must be distinct across founders when both are PINNED, "
                f"got identical value across {list(values.keys())} — the dual-founder comparison "
                f"premise (two distinct Genomes) would be broken: {values!r}"
            )

    claim_strength = data["claim_strength_target"]
    if not isinstance(claim_strength, str) or claim_strength != "C2":
        raise Run9ValidationError(f"claim_strength_target must be 'C2', got {claim_strength!r}")

    # rev 0.3 新設（User 外部レビュー PR #317 P2-2 採用）: human_audit_mode
    # は pin 欄ではなく通常欄だが、語彙は `HUMAN_AUDIT_MODES` に厳密一致を
    # 要求する（fail-closed — 未知値は構造検証の対象）。
    # ADVISORY_PREDECLARED のとき human_evaluation_protocol_sha が PINNED
    # 必須という**クロスフィールドの readiness 判定**は、ここ（構造検証層）
    # ではなく `gate_state()` が担う — PR #316 第4巡の層分離の境界宣言
    # （loader は事前登録契約の構造述語を検査する層、実測値との突合・
    # 派生的な readiness 判定は上位層の職務）をそのまま踏襲する。
    human_audit_mode = data["human_audit_mode"]
    if not isinstance(human_audit_mode, str) or human_audit_mode not in HUMAN_AUDIT_MODES:
        raise Run9ValidationError(
            f"human_audit_mode must be one of {list(HUMAN_AUDIT_MODES)}, got {human_audit_mode!r}"
        )

    # rev 0.4 新設（DESIGN_RUN9_REVISION_0.4.md、2026-08-25 User 追加裁定
    # 「確認メモ / RUN9 用語整理」指示2）: pin 欄ではなく通常欄。
    # `validate_performance_source_block()`（本モジュール後方定義、
    # Python は呼び出し時に名前解決するため前方参照で問題ない）が
    # id/role の凍結値一致 + teacher_terminology_note の非所有注記2要件
    # （Voice 所有者を意味しない旨 + Voice Source/Performance
    # Source/Performance Author の3語）を検証する。
    validate_performance_source_block(data["performance_source"])

    # deepcopy（Codex bot レビュー PR #315 第2巡指摘1採用）: `dict(data)` は
    # 浅いコピーのため、ネストした pin 欄 dict（`data["education_technique_
    # lesson_manifest_sha"]` 等）は呼び出し元の入力オブジェクトとまだ共有
    # されたままだった — 呼び出し元が
    # load 後にそのネスト dict を書き換えると `Run9RunContract.raw` も
    # 一緒に変化してしまう（validate 済みスナップショットのはずが実は
    # 可変共有だった）。deepcopy でこの共有を断つ。
    return Run9RunContract(raw=copy.deepcopy(dict(data)))


def load_run9_contract_from_yaml_text(text: str) -> Run9RunContract:
    # `yaml.safe_load()` ではなく `_StrictYAMLLoader`（重複キー fail-closed
    # 拒否）を使う — 例えば PENDING の `education_technique_lesson_
    # manifest_sha`（rev 0.3 で `lesson_sha` から改名）の後に PINNED の
    # 同名欄を書き足した手編集 contract が、標準 YAML の last-key-wins
    # 解決で検証をすり抜けて READY へ到達し得た（Codex bot レビュー
    # PR #315 第8巡指摘1採用）。
    data = yaml.load(text, Loader=_StrictYAMLLoader)
    return load_run9_contract(data)


def load_run9_contract_from_yaml_path(path: Path) -> Run9RunContract:
    return load_run9_contract_from_yaml_text(Path(path).read_text(encoding="utf-8"))


def gate_state(contract: Run9RunContract) -> str:
    """DESIGN_RUN9 §27 item 49「incomplete Hard Gate set -> BLOCKED」の
    pre-run 機械判定: pre-run 必須欄（`CONTRACT_PIN_FIELDS` から
    `CONTRACT_POST_RUN_PIN_FIELDS` と `CONTRACT_OPTIONAL_PIN_FIELDS` を
    除いた全欄 + 両 founder の founder_genome_shas）が全て PINNED のときの
    み "READY"。1つでも PENDING/BLOCKED なら "BLOCKED"。post-run 専用欄
    （artifact_manifest_sha / cost_record_sha）は判定対象外
    （実行後にのみ実測できる証拠欄のため — RUN_CONTRACT_SCHEMA_v1.json の
    x-gate-class post_run 分類と同じ考え方）。optional 欄
    （human_evaluation_protocol_sha）も同様に判定対象外だが理由が異なる
    （PR #317 Codex bot レビュー第1巡 Fix 1 採用）: rev 0.3 改訂F により
    人間知覚 Gate は必須ではなくなった — advisory な blind human audit を
    実施する場合にのみ pin する欄であり、PENDING のままでも READY を
    妨げない。post-run（「まだ実測できない」）とは異なり、optional は
    「実施しないなら永久に PENDING のままで構わない」欄。

    **例外**（rev 0.3 新設、User 外部レビュー PR #317 P2-2 採用）:
    `human_audit_mode == "ADVISORY_PREDECLARED"` を宣言した contract は、
    「監査を予定した」という意思表示そのものが実測要件になるため、
    `human_evaluation_protocol_sha` が PINNED でなければ READY にならない
    （= この場合に限り optional 除外から差し戻す）。`human_audit_mode ==
    "DISABLED"`（既定値）のときは従来どおり除外されたまま。

    `interventions.{c0_replay_takes_per_founder,c1_sham_takes_per_founder}`
    （`INTERVENTION_TAKE_COUNT_FIELDS`、Codex bot レビュー PR #318 第7巡
    Fix 20 で新設）も pre-run 必須の pin 欄として判定に含める（同 PR 第8巡
    Fix 22 採用）: これらはトップレベルではなく `interventions` 配下の
    入れ子 dict のため、`CONTRACT_PIN_FIELDS` からの `pre_run_fields`
    導出には現れない — 何もしなければ両欄が PENDING のままでも他の
    トップレベル欄が全 PINNED なら READY を返してしまい、C0/C1 校正母集団
    サイズが未凍結のまま学習が開始できてしまう（事前登録 P95 閾値が
    無効化される）。gate は構造述語（PINNED/PENDING の snapshot 判定）に
    留め、pin 値の実物照合は引き続き R9-G1 tooling の職務のまま変更しない
    — `_require_positive_int()` による値の型検証は `load_run9_contract()`
    が pin 時点で既に行っており（`intervention_take_count_field()` 経由の
    再検証がその検証済み snapshot を読むだけなので）ここで重複させない。

    毎回 `contract.raw` のスナップショットを `load_run9_contract()` で
    再検証してから判定する（Codex bot レビュー PR #315 第2巡指摘1採用）:
    呼び出し元が load 済みの `Run9RunContract.raw`（`Run9RunContract` は
    dataclass だが `raw: Dict` 自体はミュータブル）を直接書き換えて
    `status: "PINNED"` を騙っても、その改変内容は load 時と同じ
    fail-closed 検証（`_validate_pin_field` の PINNED 値整形式強制を含む）
    を再び通過しなければならない — 素通しの pin 判定だけを見ていた旧実装
    では、load 後の直接改変で READY を騙る経路が残っていた。
    """
    revalidated = load_run9_contract(contract.raw)
    _excluded_from_pre_run = CONTRACT_POST_RUN_PIN_FIELDS | CONTRACT_OPTIONAL_PIN_FIELDS
    if revalidated.raw.get("human_audit_mode") == "ADVISORY_PREDECLARED":
        # advisory 監査を予定宣言した場合のみ、optional 除外を
        # human_evaluation_protocol_sha に限って差し戻す。
        _excluded_from_pre_run = _excluded_from_pre_run - {"human_evaluation_protocol_sha"}
    pre_run_fields = [n for n in CONTRACT_PIN_FIELDS if n not in _excluded_from_pre_run]
    for name in pre_run_fields:
        if not _is_field_pinned(revalidated.pin_field(name)):
            return "BLOCKED"
    for founder_id in CONTRACT_FOUNDER_IDS:
        if not _is_field_pinned(revalidated.founder_genome_sha(founder_id)):
            return "BLOCKED"
    for take_field_name in INTERVENTION_TAKE_COUNT_FIELDS:
        if not _is_field_pinned(revalidated.intervention_take_count_field(take_field_name)):
            return "BLOCKED"
    return "READY"


# ---------------------------------------------------------------------------
# User donor rights manifest 検証（DESIGN_RUN9_REVISION_0.2.md 改訂4）。
# `inputs/rights_manifest.json` が `voice_genesis/foundry/recording_kit/
# user_donor_ledger.json` の転記として過不足なく正しいことを検証する
# loader 側ヘルパ（Codex bot レビュー PR #316 第3巡指摘, 0a4d0cf, 採用: 従来
# テストは件数一致 + ledger 側からの引き当てのみで、manifest 側の重複
# card_id や、ledger に無い card_id の混入を検出できなかった — UC-017 を
# UC-016 の複製に差し替えても、件数17・両方とも ledger 側に実在する
# card_id のため素通りしていた）。attest 後の実運用でも同じ検査が効くよう
# loader 側の関数として実装する（テストはこれを呼ぶだけにする）。
# ---------------------------------------------------------------------------

# RUN9 が対象とする User donor カードの凍結集合（Codex bot レビュー PR #316
# 第6巡指摘採用, be8f448: 変種追撃ではなく User 裁定4（2026-08-24,
# DESIGN_RUN9_REVISION_0.2.md 改訂4）の逐語「UC-001〜017」の機械化漏れの
# 是正）。旧実装は rights_manifest と donor_ledger の card_id 集合が
# **互いに** 一致することしか検証しておらず、両文書が共同で期待集合を
# 定義していたため、両側同時に UC-017 を UC-999 へ差し替えても
# （相互一致は保たれたまま）通過してしまっていた。本定数を外部の凍結
# 参照点として両側と突き合わせることでこの穴を閉じる。将来 intake が
# 増えても RUN9 の donor 集合はこの17枚で凍結する — 変更は
# design_revision を上げる別事案として扱う（本定数のハードコード改変では
# ない）。
USER_DONOR_CARD_IDS: Tuple[str, ...] = (
    "UC-001", "UC-002", "UC-003", "UC-004", "UC-005", "UC-006", "UC-007",
    "UC-008", "UC-009", "UC-010", "UC-011", "UC-012", "UC-013", "UC-014",
    "UC-015", "UC-016", "UC-017",
)


def load_rights_manifest_json(text: str) -> Dict[str, Any]:
    """`rights_manifest.json` のテキストを重複キー拒否（`_loads_strict_json()`
    — VG-E0 `models.loads_strict()` と同型の fail-closed 規約）で読み込む。

    `verify_rights_manifest_against_ledger()` への rights_manifest 入力は、
    生の `json.loads()` ではなく本関数（`load_user_donor_ledger_json()` と
    対で donor_ledger 側も同様）を経由することを規定する（Codex bot
    レビュー PR #316 第10巡指摘採用, c34bdff, 本 PR 最終レビュー対応巡）:
    手編集した rights_manifest.json の同一 entry 内に `card_id`/`sha256`
    等のキーを2回書いても、標準 `json.loads()` の last-key-wins だと
    後勝ちの値だけが黙って検証器へ届き、「たまたま期待値に潰れた」
    曖昧な生 JSON が attest によってそのまま束縛され得る — 生 JSON の
    曖昧性そのものを読込段で拒否する。
    """
    data = _loads_strict_json(text)
    if not isinstance(data, dict):
        raise Run9ValidationError(
            f"rights manifest document must be an object, got {type(data).__name__}"
        )
    return data


def load_user_donor_ledger_json(text: str) -> Dict[str, Any]:
    """`user_donor_ledger.json` のテキストを重複キー拒否で読み込む。
    `load_rights_manifest_json()` と同一規約・同一理由（Codex bot
    レビュー PR #316 第10巡指摘採用）。
    """
    data = _loads_strict_json(text)
    if not isinstance(data, dict):
        raise Run9ValidationError(
            f"donor ledger document must be an object, got {type(data).__name__}"
        )
    return data


def _require_rights_ledger_field(
    entry: Mapping[str, Any], field: str, *, side: str, card_id: str
) -> Any:
    """`entry[field]` の存在（キー自体の有無）を検証してから値を返す。
    `side` はエラーメッセージへ明記する「どちら側の entry か」のラベル
    （`"rights_manifest"` / `"donor_ledger"`）。値の型・書式は呼び出し元の
    `_require_*` ヘルパが別途検証する — 本関数は「フィールドが存在するか」
    だけを見る（Codex bot レビュー PR #316 第4巡指摘採用: `.get(field)` の
    黙った `None` フォールバックだと、両側から同じ必須フィールドが欠けた
    場合に `None == None` で照合が素通りしてしまっていた）。
    """
    if field not in entry:
        raise Run9ValidationError(
            f"{side}.entries[card_id={card_id!r}] is missing required field {field!r}"
        )
    return entry[field]


def _require_rights_ledger_sha256_hex(value: Any, *, side: str, card_id: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
        raise Run9ValidationError(
            f"{side}.entries[card_id={card_id!r}].{field} must be exactly 64 lowercase hex "
            f"characters (sha256 format), got {value!r}"
        )
    return value


def _require_rights_ledger_positive_duration(value: Any, *, side: str, card_id: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Run9ValidationError(
            f"{side}.entries[card_id={card_id!r}].duration_sec must be a number (bool rejected), "
            f"got {value!r}"
        )
    out = float(value)
    if not math.isfinite(out) or out <= 0.0:
        raise Run9ValidationError(
            f"{side}.entries[card_id={card_id!r}].duration_sec must be a positive finite number, "
            f"got {value!r}"
        )
    return out


def verify_rights_manifest_against_ledger(
    rights_manifest: Mapping[str, Any], donor_ledger: Mapping[str, Any]
) -> None:
    """`rights_manifest` の `entries` が `donor_ledger` の `entries` の
    忠実な転記（card_id/source_sha256/sha256/duration_sec）であることを
    検証する。違反は `Run9ValidationError`。

    **入力は `load_rights_manifest_json()` / `load_user_donor_ledger_json()`
    経由で読み込んだ dict を渡すこと**（生の `json.loads()` を経由しない
    — Codex bot レビュー PR #316 第10巡指摘採用, c34bdff）: 本関数自体は
    パース済み dict のみを受け取るため、呼び出し元が重複キーを黙って
    last-key-wins で解決する経路（生 `json.loads()`）で読み込んだ入力を
    渡すと、手編集 JSON 内の重複キーが「たまたま期待値に潰れた」曖昧な
    状態のまま本関数の検証を通過し得る。上記2関数は重複キーを読込段で
    拒否するため、この経路を通す限り曖昧な入力は本関数に到達しない。

    検証項目:

    1. `rights_manifest.entries` の `card_id` に重複が無いこと。
    2. `rights_manifest.entries` の card_id 集合が `donor_ledger.entries`
       の card_id 集合と**完全一致**すること（過不足を両方向とも検出 —
       manifest 側に無い ledger の card_id・manifest 側にしか無い
       card_id のいずれも拒否）。
    3. 一致する各 card_id について `source_sha256`/`sha256`/
       `duration_sec` が、**両側で存在 + 整形式であることを照合前に
       強制した上で**、ledger 側の実測値とバイト/値レベルで一致する
       こと（sha 系は64hex str、duration_sec は bool でない正の有限
       数値）。存在・整形式のいずれかが欠けた側は、比較を試みる前に
       `Run9ValidationError` で拒否する（Codex bot レビュー PR #316
       第4巡指摘採用: 従来は `entry.get(field)` 同士の等値比較だけの
       ため、rights entry と ledger entry の両方から同じ必須
       フィールドが欠落すると `None == None` が真になり、両側欠落を
       検出できなかった）。
    4. `rights_manifest.schema == "run9-user-donor-rights/1.0"` /
       `donor_ledger.schema == "user-donor-ledger/0.1"`（欠落・別値・
       非 str はいずれも拒否 — Codex bot レビュー PR #316 第5巡指摘A
       採用: 意味論を理解しない版の文書を attest 経由で
       `anchor_hashes.user` へ正典束縛し得るため、版の取り違えは
       card_id/値の一致以前に拒否する）。
    5. `donor_ledger.entries` の `card_id` にも重複が無いこと（Codex bot
       レビュー PR #316 第5巡指摘B採用: 第3巡は rights 側のみ重複拒否
       しており、ledger 側は `ledger_by_id[card_id] = entry` の
       last-entry-wins で曖昧な ledger を黙って解決していた非対称が
       残っていた）。
    6. rights_manifest・donor_ledger **双方**の card_id 集合が、外部の
       凍結参照点 `USER_DONOR_CARD_IDS`（UC-001〜UC-017 の17枚、User 裁定
       4・2026-08-24 の逐語固定）と完全一致すること（Codex bot レビュー
       PR #316 第6巡指摘採用, be8f448: 変種追撃ではなく User 裁定4の
       機械化漏れの是正 — 従来は rights/ledger の**相互**一致しか
       検証しておらず、両文書が期待集合を共同定義していたため、両側
       同時に UC-017 を UC-999 のような別 ID へ差し替えても相互一致は
       保たれたまま通過してしまっていた）。将来 intake が増えても RUN9
       の donor 集合はこの17枚で凍結する — 変更は design_revision を
       上げる別事案として扱う。

    rights 検証器の堅牢化ファミリー（PR #316 第3〜6巡: card_id 完全一致・
    両側存在+整形式・schema 版・ledger 側重複拒否・期待集合の凍結）は
    本巡で全数掃討・終端する。以降に見つかる同型変種（本ファミリーが
    扱う対称性の範囲外の新しい欠陥クラス）は、都度追撃せず境界宣言で
    扱う。
    """
    rights_schema = rights_manifest.get("schema")
    if not isinstance(rights_schema, str) or rights_schema != "run9-user-donor-rights/1.0":
        raise Run9ValidationError(
            "rights_manifest.schema must be exactly 'run9-user-donor-rights/1.0', got "
            f"{rights_schema!r} (a document declaring a different or missing schema version "
            "must not be treated as this contract's rights manifest, since anchor_hashes.user "
            "binding depends on this schema's exact semantics)"
        )
    ledger_schema = donor_ledger.get("schema")
    if not isinstance(ledger_schema, str) or ledger_schema != "user-donor-ledger/0.1":
        raise Run9ValidationError(
            "donor_ledger.schema must be exactly 'user-donor-ledger/0.1', got "
            f"{ledger_schema!r}"
        )

    rights_entries_raw = rights_manifest.get("entries")
    if not isinstance(rights_entries_raw, list):
        raise Run9ValidationError(
            f"rights_manifest.entries must be a list, got {type(rights_entries_raw).__name__}"
        )
    ledger_entries_raw = donor_ledger.get("entries")
    if not isinstance(ledger_entries_raw, list):
        raise Run9ValidationError(
            f"donor_ledger.entries must be a list, got {type(ledger_entries_raw).__name__}"
        )

    rights_card_ids: List[str] = []
    rights_by_id: Dict[str, Mapping[str, Any]] = {}
    for i, entry in enumerate(rights_entries_raw):
        if not isinstance(entry, dict):
            raise Run9ValidationError(f"rights_manifest.entries[{i}] must be an object")
        card_id = entry.get("card_id")
        if not isinstance(card_id, str) or not card_id:
            raise Run9ValidationError(
                f"rights_manifest.entries[{i}].card_id must be a non-empty string, got {card_id!r}"
            )
        rights_card_ids.append(card_id)
        rights_by_id[card_id] = entry

    # item 1: rights_manifest 側の card_id 重複拒否（len(ids) == len(set(ids))）。
    if len(rights_card_ids) != len(set(rights_card_ids)):
        seen: set = set()
        duplicates = sorted({c for c in rights_card_ids if c in seen or seen.add(c)})
        raise Run9ValidationError(
            f"rights_manifest.entries has duplicate card_id value(s): {duplicates} "
            "(each donor card must appear exactly once)"
        )

    ledger_card_ids: List[str] = []
    ledger_by_id: Dict[str, Mapping[str, Any]] = {}
    for i, entry in enumerate(ledger_entries_raw):
        if not isinstance(entry, dict):
            raise Run9ValidationError(f"donor_ledger.entries[{i}] must be an object")
        card_id = entry.get("card_id")
        if not isinstance(card_id, str) or not card_id:
            raise Run9ValidationError(
                f"donor_ledger.entries[{i}].card_id must be a non-empty string, got {card_id!r}"
            )
        ledger_card_ids.append(card_id)
        ledger_by_id[card_id] = entry

    # item 5: donor_ledger 側の card_id 重複拒否（rights 側と対称。旧実装は
    # `ledger_by_id[card_id] = entry` の last-entry-wins で曖昧 ledger を
    # 黙って解決していた — Codex bot レビュー PR #316 第5巡指摘B）。
    if len(ledger_card_ids) != len(set(ledger_card_ids)):
        seen_ledger: set = set()
        ledger_duplicates = sorted(
            {c for c in ledger_card_ids if c in seen_ledger or seen_ledger.add(c)}
        )
        raise Run9ValidationError(
            f"donor_ledger.entries has duplicate card_id value(s): {ledger_duplicates} "
            "(an ambiguous ledger must not be silently resolved via last-entry-wins)"
        )

    # item 2: card_id 集合の完全一致（過不足を両方向とも検出）。
    rights_id_set = set(rights_by_id.keys())
    ledger_id_set = set(ledger_by_id.keys())
    missing_from_rights = sorted(ledger_id_set - rights_id_set)
    extra_in_rights = sorted(rights_id_set - ledger_id_set)
    if missing_from_rights or extra_in_rights:
        raise Run9ValidationError(
            "rights_manifest.entries card_id set does not exactly match donor_ledger.entries "
            f"card_id set — missing_from_rights={missing_from_rights!r} "
            f"extra_in_rights={extra_in_rights!r}"
        )

    # item 6: 両側とも外部の凍結参照点 USER_DONOR_CARD_IDS と完全一致する
    # こと（item 2 の相互一致だけでは、両側が同時に同じ ID へ差し替わる
    # 攻撃を検出できない — Codex bot レビュー PR #316 第6巡指摘）。
    expected_id_set = set(USER_DONOR_CARD_IDS)
    rights_unexpected = sorted(rights_id_set - expected_id_set)
    rights_absent = sorted(expected_id_set - rights_id_set)
    if rights_unexpected or rights_absent:
        raise Run9ValidationError(
            "rights_manifest.entries card_id set does not exactly match the frozen "
            f"USER_DONOR_CARD_IDS set (UC-001..UC-017) — unexpected={rights_unexpected!r} "
            f"absent={rights_absent!r}"
        )
    ledger_unexpected = sorted(ledger_id_set - expected_id_set)
    ledger_absent = sorted(expected_id_set - ledger_id_set)
    if ledger_unexpected or ledger_absent:
        raise Run9ValidationError(
            "donor_ledger.entries card_id set does not exactly match the frozen "
            f"USER_DONOR_CARD_IDS set (UC-001..UC-017) — unexpected={ledger_unexpected!r} "
            f"absent={ledger_absent!r}"
        )

    # item 3: 一致する card_id ごとの値照合。存在 + 整形式を両側で強制して
    # から比較する（`None == None` すり抜けの防止）。
    for card_id, rights_entry in rights_by_id.items():
        ledger_entry = ledger_by_id[card_id]

        for field in ("source_sha256", "sha256"):
            rights_raw = _require_rights_ledger_field(
                rights_entry, field, side="rights_manifest", card_id=card_id
            )
            ledger_raw = _require_rights_ledger_field(
                ledger_entry, field, side="donor_ledger", card_id=card_id
            )
            rights_value = _require_rights_ledger_sha256_hex(
                rights_raw, side="rights_manifest", card_id=card_id, field=field
            )
            ledger_value = _require_rights_ledger_sha256_hex(
                ledger_raw, side="donor_ledger", card_id=card_id, field=field
            )
            if rights_value != ledger_value:
                raise Run9ValidationError(
                    f"rights_manifest.entries[card_id={card_id!r}].{field} does not match "
                    f"donor_ledger: rights={rights_value!r} ledger={ledger_value!r}"
                )

        rights_duration_raw = _require_rights_ledger_field(
            rights_entry, "duration_sec", side="rights_manifest", card_id=card_id
        )
        ledger_duration_raw = _require_rights_ledger_field(
            ledger_entry, "duration_sec", side="donor_ledger", card_id=card_id
        )
        rights_duration = _require_rights_ledger_positive_duration(
            rights_duration_raw, side="rights_manifest", card_id=card_id
        )
        ledger_duration = _require_rights_ledger_positive_duration(
            ledger_duration_raw, side="donor_ledger", card_id=card_id
        )
        if rights_duration != ledger_duration:
            raise Run9ValidationError(
                f"rights_manifest.entries[card_id={card_id!r}].duration_sec does not match "
                f"donor_ledger: rights={rights_duration!r} ledger={ledger_duration!r}"
            )


# ---------------------------------------------------------------------------
# rev 0.4（DESIGN_RUN9_REVISION_0.4.md 変更1・2）: rights_manifest.json の
# 4層構造（voice_identity_rights/performance_rights/composition_rights/
# recording_master_rights）。`verify_rights_manifest_against_ledger()` 自体
# は変更しない（既存テストの後方互換を保つ）— 代わりに、4層文書から
# voice_identity_rights 層を取り出し、`verify_rights_manifest_against_ledger()`
# が期待する旧 schema `run9-user-donor-rights/1.0` 相当のフラット構造へ
# 変換するアダプタを新設する。
# ---------------------------------------------------------------------------

SCHEMA_RIGHTS_MANIFEST_FOUR_LAYER = "run9-rights-manifest/2.0"

# 派生設計変更メモ変更2の4層名（逐語キー、順序は派生設計変更メモの雛形順）。
RIGHTS_MANIFEST_LAYER_NAMES: Tuple[str, str, str, str] = (
    "voice_identity_rights",
    "performance_rights",
    "composition_rights",
    "recording_master_rights",
)

# 派生設計変更メモ変更1「原則」の逐語3式。
RIGHTS_MANIFEST_PRINCIPLES: Tuple[str, str, str] = (
    "Teacher ≠ Voice Identity Owner",
    "Teacher ≠ Performance Author",
    "Voice Source ≠ Performance Source",
)

_RIGHTS_MANIFEST_FOUR_LAYER_TOP_KEYS: FrozenSet[str] = frozenset(
    {"schema", "revision_note", "principles", "auto_interpretation_prohibited", "hard_gate", "history"}
    | set(RIGHTS_MANIFEST_LAYER_NAMES)
)

# 派生設計変更メモ変更1の provenance ネストブロック（逐語ブロック名 + 値
# フィールド名）。Codex bot レビュー PR #319 第1巡指摘2（P2）採用: 旧
# validate_rights_manifest_four_layer() は各層の `provenance` が dict で
# あることしか見ておらず、`provenance: {}` やブロック欠落を素通りさせて
# いた（DESIGN_RUN9_REVISION_0.4.md の実際の欠落 — rights_manifest.json
# recording_master_rights.provenance に synthesis ブロックが無いまま
# valid-file テストが green だった事実で顕在化）。ここでブロック形状を
# 閉集合として凍結する。
#
# 各フィールドは「値の種別」（`_RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL` =
# 外部の第三者事実 / `_RIGHTS_MANIFEST_FIELD_KIND_USER` = User 自身が
# attest すべき対象）を伴う（2026-08-25 User 追加裁定②が要求する語彙分離
# — PJS Performance Source に関する4ブロックはいずれも外部の第三者
# （PJS corpus・その著者・演者）に関する事実であり、User 自身の権利・
# 許諾についての欄ではないため、現行スキーマでは全フィールドが external
# 種別になる。将来 User 自身が主体となる provenance ブロックを追加する
# 場合のみ user 種別が使われる）。
_RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL = "external"
_RIGHTS_MANIFEST_FIELD_KIND_USER = "user"

_RIGHTS_MANIFEST_PROVENANCE_BLOCK_VALUE_KEYS: Dict[str, Dict[str, str]] = {
    "voice_source": {
        "owner": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
        "source_id": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
    },
    "performance_author": {
        "performer": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
        "performance_editor": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
    },
    "synthesis": {
        "engine": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
        "voicebank": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
    },
    "composition": {
        "composer": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
        "lyricist": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
    },
}

# 層レベルの rights_class/consent_status 値語彙（Codex bot レビュー PR #319
# 第2巡指摘, Fix 5, P2, 採用）: provenance ブロック内の値語彙は角括弧付き
# placeholder `<...>` を使う（_RIGHTS_MANIFEST_PENDING_USER_ATTESTATION /
# _RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL、下記で定義）のに対し、層直下の
# rights_class/consent_status は角括弧なしの裸トークンを使う——既存実装の
# 流儀（voice_identity_rights.rights_class の値 "PENDING_USER_ATTESTATION"、
# rev 0.2 由来）に合わせて一貫させる。2つの規約は別物であり混同しない。
_RIGHTS_MANIFEST_STATUS_PENDING_USER_ATTESTATION = "PENDING_USER_ATTESTATION"
_RIGHTS_MANIFEST_STATUS_UNRESOLVED_EXTERNAL = "UNRESOLVED_EXTERNAL"

# Codex bot レビュー PR #319 第11巡指摘（P2, 採用, Fix 24）: attested 形態の
# 判定（`_validate_rights_manifest_voice_identity_attestation`）は従来
# 「両 status が PENDING_USER_ATTESTATION と異なる」ことしか要求しておらず、
# `rights_class`/`consent_status` を `DENIED` 等の任意・矛盾値へ書き換えても
# 受理してしまっていた——承認記録付き `granted` usage grant（Fix 19 前提条件
# ①）と組み合わせると、権利状態が否認/未記述のまま raw 公開・配布を許可する
# 正典 manifest が通ってしまう。
# 現物確認: `inputs/rights_manifest.json` / DESIGN_RUN9_REVISION_0.2.md
# 改訂4 / DESIGN_RUN9_REVISION_0.4.md（変更1・2「attest 対象の更新」節）の
# いずれにも attested 後の意図された status 値の規定はない。ただし
# `tests/test_run9_contract.py`（Fix 16/19 導入時点、`test_fix319_16_valid_
# attested_form_accepted` 他 7 箇所）は既に `rights_class`/`consent_status`
# 双方に "USER_ATTESTED_OWN_VOICE" を使う規約を現物として確立済み——これを
# 閉語彙として凍結する（新規裁定で非対称な値を導入し既存回帰と乖離させない）。
# 閉語彙は各1値の閉集合: 拒否・撤回等の他状態は将来の design revision で
# 語彙追加するまで表現不能（fail-closed）。
_RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE = "USER_ATTESTED_OWN_VOICE"

# 層ごとの主体種別: voice_identity_rights のみ User 帰属（User donor 自身
# の声の権利。attestation 主体 = User）で、他3層（performance/composition/
# recording_master）はいずれも PJS という外部第三者に関する権利であり
# external 種別（provenance ブロックの
# _RIGHTS_MANIFEST_PROVENANCE_BLOCK_VALUE_KEYS が同3層の全フィールドを
# external としているのと同じ帰属根拠 — Fix 6 の仕分け）。
_RIGHTS_MANIFEST_LAYER_FIELD_KIND: Dict[str, str] = {
    "voice_identity_rights": _RIGHTS_MANIFEST_FIELD_KIND_USER,
    "performance_rights": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
    "composition_rights": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
    "recording_master_rights": _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL,
}

# 層ごとの必須キー閉集合（Codex bot レビュー PR #319 第2巡指摘, Fix 5, P2,
# 採用）: 旧 validate_rights_manifest_four_layer() は非空 role と
# provenance ブロックの形状しか要求せず、recording_master_rights.license /
# performance_rights.rights_class / 各層の consent_status を削除しても
# validator が受理してしまっていた（permission 系フィールドが構造的に
# 必須になっていなかった）。inputs/rights_manifest.json の実キーを読み、
# 各層が持つべきキー集合を層別の閉集合として凍結する（欠落・未知キーの
# いずれも拒否 — fail-closed）。
_RIGHTS_MANIFEST_LAYER_REQUIRED_KEYS: Dict[str, FrozenSet[str]] = {
    "voice_identity_rights": frozenset(
        {
            "role",
            "schema_legacy",
            "donor_ledger_source",
            "donor_ledger_schema",
            "transcribed_at",
            "note",
            "entries",
            "rights_class",
            "consent_status",
            "attestation",
            "usage_grants",
            "usage_grants_note",
            "binding_note",
        }
    ),
    "performance_rights": frozenset(
        {
            "role",
            "performance_source",
            "provenance",
            "rights_class",
            "consent_status",
            "consent_status_note",
        }
    ),
    "composition_rights": frozenset({"role", "provenance", "rights_class", "consent_status"}),
    "recording_master_rights": frozenset(
        {
            "role",
            "provenance",
            "license",
            "interpretations",
            "corpus_pins",
            "rights_class",
            "consent_status",
        }
    ),
}

# Codex bot レビュー PR #319 第3巡指摘（P2, 採用, Fix 8）: 層別必須キー
# 閉集合（上記 `_RIGHTS_MANIFEST_LAYER_REQUIRED_KEYS`）はキーの**存在**しか
# 強制しておらず、`recording_master_rights.license` を `{}` や任意の
# スカラー値へ置換しても validator が受理してしまっていた——CC BY-SA 4.0
# のライセンス種別・適用範囲・義務・出典（`inputs/rights_manifest.json`
# 実データの4キー）が消えても構造的に valid のままだった。ここでネスト
# object 値の**形状**（閉じたキー集合 + 各値が非空文字列であること）を
# 凍結する。同じ「存在のみ検証の object 値」欠陥は
# `voice_identity_rights.usage_grants`・`recording_master_rights.
# interpretations` の各エントリ・`recording_master_rights.corpus_pins`
# にも存在したため、同じ流儀でネスト形状検証を追加する（`usage_cards` は
# 本スキーマに存在しないキーのため対象外——現物の実キーのみを閉集合化し、
# 過剰一般化はしない）。
_RIGHTS_MANIFEST_LICENSE_BLOCK_KEYS: FrozenSet[str] = frozenset(
    {"value", "scope", "derivative_obligation", "source"}
)
_RIGHTS_MANIFEST_USAGE_GRANTS_KEYS: FrozenSet[str] = frozenset(
    {"run9_identity_anchor", "raw_audio_publication", "model_general_distribution"}
)
_RIGHTS_MANIFEST_INTERPRETATION_ENTRY_KEYS: FrozenSet[str] = frozenset(
    {"status", "question", "note", "source"}
)
_RIGHTS_MANIFEST_CORPUS_PINS_TOP_KEYS: FrozenSet[str] = frozenset(
    {"source_archive_sha256", "expanded_corpus_identity_sha256", "note"}
)
_RIGHTS_MANIFEST_CORPUS_PIN_SUB_KEYS: FrozenSet[str] = frozenset(
    {"source_archive_sha256", "expanded_corpus_identity_sha256"}
)
_RIGHTS_MANIFEST_CORPUS_PIN_ENTRY_KEYS: FrozenSet[str] = frozenset({"value", "source"})


def _validate_closed_string_object(path: str, obj: Any, required_keys: FrozenSet[str]) -> None:
    """`obj` が `required_keys` の閉集合ちょうどを持ち、全値が非空文字列で
    あることを検証する共通ヘルパー（Fix 8）。license/usage_grants/
    interpretations エントリ/corpus_pins サブブロックが共有する「値が
    すべて非空文字列の閉じた object」という同型の形状検証を重複実装しない
    ための集約。未知キー・欠落キー・非 dict・空文字列・非文字列値の
    いずれも fail-closed で拒否する。
    """
    if not isinstance(obj, dict):
        raise Run9ValidationError(f"{path} must be an object, got {type(obj).__name__}")
    unknown = set(obj.keys()) - required_keys
    if unknown:
        raise Run9ValidationError(f"{path} has unknown key(s): {sorted(unknown)}")
    missing = required_keys - set(obj.keys())
    if missing:
        raise Run9ValidationError(f"{path} missing required key(s): {sorted(missing)}")
    for key in sorted(required_keys):
        value = obj[key]
        if not isinstance(value, str) or not value.strip():
            raise Run9ValidationError(f"{path}.{key} must be a non-empty string, got {value!r}")


def _validate_rights_manifest_license_block(license_block: Any) -> None:
    """`recording_master_rights.license` のネスト形状を検証する（Codex bot
    レビュー PR #319 第3巡指摘（P2, 採用, Fix 8）: 旧
    `validate_rights_manifest_four_layer()` は層の必須キー閉集合で
    `license` キーの存在は強制していたが、値の中身
    （`value`/`scope`/`derivative_obligation`/`source`）は一切検証しておらず、
    `license: {}` やスカラー値へ置換しても構造的に valid のまま通過して
    いた——CC BY-SA 4.0 のライセンス種別・適用範囲・義務・出典が消えても
    検出できなかった。`inputs/rights_manifest.json` の実キー4つを閉集合と
    して凍結し、いずれも非空文字列であることを強制する。
    """
    _validate_closed_string_object(
        "rights manifest.recording_master_rights.license",
        license_block,
        _RIGHTS_MANIFEST_LICENSE_BLOCK_KEYS,
    )


def _validate_rights_manifest_corpus_pins_block(corpus_pins: Any) -> None:
    """`recording_master_rights.corpus_pins` のネスト形状を検証する（Fix 8。
    64hex 形式検証は Codex bot レビュー PR #319 第10巡指摘, P2, 採用, Fix 23）。
    `source_archive_sha256`/`expanded_corpus_identity_sha256`（各 `value`/
    `source` の2キー object）+ `note` の3キー閉集合。source archive pin
    と expanded corpus pin は互いに代替ではない別対象の2値（rev 0.2 改訂3）
    であり、いずれかが欠落・スカラー化しても validator が受理してはならない。
    Fix 8 時点では各 `value` は非空 str であることしか検証しておらず、
    `"x"` のような使用不能な値でも構造的に valid のまま通過していた——
    `_SHA256_HEX_RE`（既存の 64hex 検証ヘルパー、`_require_manifest_sha256_hex`
    等と同一正規表現を再利用）で lowercase 64-hex 形式を追加強制する。
    """
    path = "rights manifest.recording_master_rights.corpus_pins"
    if not isinstance(corpus_pins, dict):
        raise Run9ValidationError(f"{path} must be an object, got {type(corpus_pins).__name__}")
    unknown = set(corpus_pins.keys()) - _RIGHTS_MANIFEST_CORPUS_PINS_TOP_KEYS
    if unknown:
        raise Run9ValidationError(f"{path} has unknown key(s): {sorted(unknown)}")
    missing = _RIGHTS_MANIFEST_CORPUS_PINS_TOP_KEYS - set(corpus_pins.keys())
    if missing:
        raise Run9ValidationError(f"{path} missing required key(s): {sorted(missing)}")
    note = corpus_pins["note"]
    if not isinstance(note, str) or not note.strip():
        raise Run9ValidationError(f"{path}.note must be a non-empty string, got {note!r}")
    for sub_key in sorted(_RIGHTS_MANIFEST_CORPUS_PIN_SUB_KEYS):
        sub_path = f"{path}.{sub_key}"
        _validate_closed_string_object(
            sub_path, corpus_pins[sub_key], _RIGHTS_MANIFEST_CORPUS_PIN_ENTRY_KEYS
        )
        pin_value = corpus_pins[sub_key]["value"]
        if not isinstance(pin_value, str) or not _SHA256_HEX_RE.match(pin_value):
            raise Run9ValidationError(
                f"{sub_path}.value must be exactly 64 lowercase hex characters (sha256 "
                f"format), got {pin_value!r}"
            )


# Codex bot レビュー PR #319 第8巡指摘（P2, 採用, Fix 16）: `attestation` は
# `_RIGHTS_MANIFEST_LAYER_REQUIRED_KEYS["voice_identity_rights"]` によって
# キーの**存在**しか強制されておらず、`{}` やスカラー、あるいは signer/
# timestamp/statement を欠いた `{"attested": true}` へ置換しても旧
# `validate_rights_manifest_four_layer()` が受理してしまっていた——User
# rights 遷移（PENDING_USER_ATTESTATION → 実際の attest 実施）を裏付ける
# 証拠が構造的に valid のまま消える。`inputs/rights_manifest.json` の実
# キー（`attested`/`attested_by`/`attested_at`/`statement`）を閉集合として
# 凍結し、pending/attested の2形態のみを許可する:
#   - pending 形態（現状 = PENDING_USER_ATTESTATION）: `attested` が
#     `False` で、将来 attest 時に埋まる `attested_by`/`attested_at`/
#     `statement` はいずれも `None`（未実施の宣言 — 実データの現物形態）
#   - attested 形態: `attested` が `True` で、`attested_by`（signer）/
#     `attested_at`（UTC ISO 8601 timestamp — repo 規約
#     `CLAUDE.md` 「タイムスタンプ: UTC, ISO 8601 形式で保存」）/
#     `statement`（非空 str）がすべて埋まっている
# さらに層の `rights_class`/`consent_status`（Fix 5 で語彙検証済み）と
# attestation の形態が矛盾しないことを検証する——`PENDING_USER_ATTESTATION`
# なのに attestation が attested 形、またはその逆（attested 形の
# rights_class/consent_status なのに attestation が pending 形）を fail-closed
# で拒否する。
_RIGHTS_MANIFEST_ATTESTATION_KEYS: FrozenSet[str] = frozenset(
    {"attested", "attested_by", "attested_at", "statement"}
)
# UTC ISO 8601 タイムスタンプ（`Z` サフィックス必須 — repo 規約に合わせる）。
_RIGHTS_MANIFEST_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
)


# Codex bot レビュー PR #319 第10巡指摘（P2, 採用, Fix 22）: 上記正規形
# チェックは桁配置しか見ておらず、`2026-99-99T99:99:99Z` のような実在
# しない日時（月/日/時/分/秒が暦として成立しない値）を通してしまう。
# 正規形強制の後段で `datetime.fromisoformat`（Python 3.11+ は `Z`
# サフィックスをそのまま受理する）により実在日時としてパース可能か
# どうかを追加検証する。`attested_at`（Fix 16）/ `approved_at`（Fix 19）
# の両方で共有する。
def _is_real_utc_timestamp(value: str) -> bool:
    """`value` が正規形（`_RIGHTS_MANIFEST_UTC_TIMESTAMP_RE`）に一致する
    前提で、暦として実在する日時かどうかを判定する（例: 月 99 / 日 99 /
    時 99 / 2 月 30 日はいずれも False）。"""
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _validate_rights_manifest_voice_identity_attestation(layer: Mapping[str, Any]) -> None:
    """`voice_identity_rights.attestation` の形状 + pending/attested 二形態の
    整合を検証する（Fix 16。両 status 要求化は Codex bot レビュー PR #319
    第10巡指摘, P2, 採用, Fix 21。attested 形態の閉語彙化は同第11巡指摘,
    P2, 採用, Fix 24）。

    旧実装は `rights_class == PENDING or consent_status == PENDING` の
    `or` 判定で pending 形態を認定していた——`consent_status` だけを
    `USER_ATTESTED_OWN_VOICE` 等へ書き換え `rights_class` は
    `PENDING_USER_ATTESTATION` のまま・`attested=false` のままでも
    「どちらかが pending」を満たすため通過してしまい、attestation
    なしに正典 permission フィールドの一部が完了を主張できていた。
    pending 形態は**両方**が `PENDING_USER_ATTESTATION` であること、
    attested 形態は**どちらも** `PENDING_USER_ATTESTATION` でないことを
    要求し、片方のみが確定化した中間状態はどちらの方向であっても
    form mismatch として拒否する。

    Fix 24: 上記の「`PENDING_USER_ATTESTATION` と異なる」という条件は
    `rights_class`/`consent_status` に `DENIED` 等の任意・矛盾値を許してしまい、
    権利状態が否認/未記述のまま attested 形態が成立できていた
    （`granted` usage grant の前提条件①＝attested 形態、と組み合わさると
    実害化する）。attested 形態は**両方**が閉語彙値
    `_RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE`
    （"USER_ATTESTED_OWN_VOICE"）と厳密一致することを要求する——
    `DENIED`・任意文字列はもちろん、旧条件では通っていた
    `PENDING_USER_ATTESTATION` 以外の非閉語彙値もすべて拒否する。
    閉語彙は各1値の閉集合であり、拒否・撤回等の他状態は将来の design
    revision で語彙追加するまで表現不能（fail-closed）。
    """
    path = "rights manifest.voice_identity_rights.attestation"
    attestation = layer.get("attestation")
    if not isinstance(attestation, dict):
        raise Run9ValidationError(f"{path} must be an object, got {type(attestation).__name__}")
    unknown = set(attestation.keys()) - _RIGHTS_MANIFEST_ATTESTATION_KEYS
    if unknown:
        raise Run9ValidationError(f"{path} has unknown key(s): {sorted(unknown)}")
    missing = _RIGHTS_MANIFEST_ATTESTATION_KEYS - set(attestation.keys())
    if missing:
        raise Run9ValidationError(f"{path} missing required key(s): {sorted(missing)}")

    attested = attestation["attested"]
    if not isinstance(attested, bool):
        raise Run9ValidationError(f"{path}.attested must be a bool, got {attested!r}")

    rights_class = layer.get("rights_class")
    consent_status = layer.get("consent_status")
    rights_class_is_pending = rights_class == _RIGHTS_MANIFEST_STATUS_PENDING_USER_ATTESTATION
    consent_status_is_pending = (
        consent_status == _RIGHTS_MANIFEST_STATUS_PENDING_USER_ATTESTATION
    )

    if not attested:
        for key in ("attested_by", "attested_at", "statement"):
            if attestation[key] is not None:
                raise Run9ValidationError(
                    f"{path}.{key} must be null while attested is false (pending form), "
                    f"got {attestation[key]!r}"
                )
        if not (rights_class_is_pending and consent_status_is_pending):
            raise Run9ValidationError(
                f"{path} is in pending form (attested=false) but rights manifest."
                f"voice_identity_rights.rights_class/consent_status are not BOTH "
                f"{_RIGHTS_MANIFEST_STATUS_PENDING_USER_ATTESTATION!r} "
                f"(rights_class={rights_class!r}, consent_status={consent_status!r}) — "
                "status/attestation form mismatch"
            )
        return

    attested_by = attestation["attested_by"]
    if not isinstance(attested_by, str) or not attested_by.strip():
        raise Run9ValidationError(
            f"{path}.attested_by must be a non-empty string (signer) when attested is true, "
            f"got {attested_by!r}"
        )
    attested_at = attestation["attested_at"]
    if (
        not isinstance(attested_at, str)
        or not _RIGHTS_MANIFEST_UTC_TIMESTAMP_RE.match(attested_at)
        or not _is_real_utc_timestamp(attested_at)
    ):
        raise Run9ValidationError(
            f"{path}.attested_at must be a UTC ISO 8601 timestamp denoting a real "
            f"calendar date/time (e.g. '2026-08-25T00:00:00Z') when attested is true, "
            f"got {attested_at!r}"
        )
    statement = attestation["statement"]
    if not isinstance(statement, str) or not statement.strip():
        raise Run9ValidationError(
            f"{path}.statement must be a non-empty string when attested is true, got {statement!r}"
        )
    rights_class_is_attested_vocab = (
        rights_class == _RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE
    )
    consent_status_is_attested_vocab = (
        consent_status == _RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE
    )
    if not (rights_class_is_attested_vocab and consent_status_is_attested_vocab):
        raise Run9ValidationError(
            f"{path} is in attested form (attested=true) but rights manifest."
            f"voice_identity_rights.rights_class/consent_status are not BOTH the closed-vocab "
            f"attested value {_RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE!r} "
            f"(rights_class={rights_class!r}, consent_status={consent_status!r}) — "
            "status/attestation form mismatch (Fix 24: arbitrary/contradictory values such as "
            "'DENIED' are no longer accepted for the attested form)"
        )


# Codex bot レビュー PR #319 第9巡指摘（P2, 採用, Fix 19）: Fix 8 は
# usage_grants の3キー閉集合 + 非空文字列という**形状**のみを検証しており、
# 値そのものは任意の非空文字列を受理していた——
# `raw_audio_publication`/`model_general_distribution` を手編集で
# `"granted"` へ書き換えても、`attestation.attested` が `false` のまま
# validator を通過してしまう。rev 0.2 改訂4（DESIGN_RUN9_REVISION_0.2.md
# 194-199行）は「raw 音源の公開・モデルの一般配布は別承認まで独立に
# not_granted 維持」と規定しており、承認証拠なしの公開/配布許可は
# その規約への違反になる。値語彙を `{not_granted, granted}` の閉集合へ
# 凍結し、`granted` への遷移には以下2条件を fail-closed で強制する:
#   ①attestation が attested 形態（`attested=true`、Fix 16 の二形態検証）
#     であること
#   ②当該 grant 専用の承認記録（`<grant_key>_approval` — grant ごとに
#     独立、`raw_audio_publication` を granted にしても
#     `model_general_distribution` の承認を兼ねない）が存在し、
#     承認日時（UTC ISO 8601）+ 承認文言（非空 str）を備えること
# run9_identity_anchor もこの3キーの一つであり、当初は同じ2条件を機械的に
# 適用した（3キーへ特例なく同一の前提条件を適用する構造そのものが、
# 「run9_identity_anchor だけ別ルールで granted 化できてしまう」抜け道を
# 作らないという狙い）。現行 manifest（3キーとも `not_granted`）はこの
# 前提条件を要求しない not_granted 形のまま不変で通る。
#
# Codex bot レビュー PR #319 第14巡指摘（P2, 採用, Fix 27）——上記「特例
# なく同一」裁定の訂正: DESIGN_RUN9_REVISION_0.2.md 194-203行（改訂4）が
# 定める正典フローは、rights manifest 記載の**別承認**を要求するのは
# `raw_audio_publication`（raw 音源の公開）と `model_general_distribution`
# （モデルの一般配布）の2件のみであり、`run9_identity_anchor`（RUN9 内部
# での identity anchor 使用）は「User attest 完了後、anchor の grant が
# それに束縛される」という記述（196-199行）であって、attestation 自体が
# anchor grant の根拠——別途の承認記録は要求されない。第9巡裁定（Fix 19）
# が3キー一律で承認記録を必須化したのは過剰一般化であり、User が規定
# どおり attest を完了して `run9_identity_anchor` を `granted` にする
# 正常遷移を、根拠のない `run9_identity_anchor_approval` 捏造なしには
# 通過できなくしてしまっていた。
#
# 訂正後の規則（grant 別）:
#   `raw_audio_publication` / `model_general_distribution`
#     （`_RIGHTS_MANIFEST_USAGE_GRANTS_REQUIRING_SEPARATE_APPROVAL`）:
#     従来どおり①attested 形態 ②grant 専用の承認記録（`<grant>_approval`）
#     の両方を要求する（rev 0.2 改訂4の「別承認」規定はこの2キーのみに
#     適用される）。
#   `run9_identity_anchor`: ①attested 形態のみを要求する。承認記録
#     （`run9_identity_anchor_approval`）は要求しない——存在しなくても
#     `granted` 遷移は受理する。仮に付与されていても拒否はしない
#     （`run9_identity_anchor_approval` は `_RIGHTS_MANIFEST_USAGE_GRANTS_
#     KEYS` から機械的に導出される既存の閉集合 `allowed_keys` に元々
#     含まれているキーであり、「規定にない記録の混入は未知キーとして
#     拒否する」流儀を適用すると該当しない——閉集合に既にあるキーを
#     追加の必須性チェックなしに通す方が、正典フローが求めない証跡の
#     捏造を誘発しないぶん安全と判断した）。付与されている場合は
#     `_validate_rights_manifest_usage_grant_approval_record()` で形状を
#     検証する（ゴミデータの無検証通過は防ぐが、必須にはしない）。
_RIGHTS_MANIFEST_USAGE_GRANT_NOT_GRANTED = "not_granted"
_RIGHTS_MANIFEST_USAGE_GRANT_GRANTED = "granted"
_RIGHTS_MANIFEST_USAGE_GRANT_VALUES: FrozenSet[str] = frozenset(
    {_RIGHTS_MANIFEST_USAGE_GRANT_NOT_GRANTED, _RIGHTS_MANIFEST_USAGE_GRANT_GRANTED}
)
_RIGHTS_MANIFEST_USAGE_GRANT_APPROVAL_SUFFIX = "_approval"
_RIGHTS_MANIFEST_USAGE_GRANT_APPROVAL_KEYS: FrozenSet[str] = frozenset(
    {"approved_at", "approval_statement"}
)
# Fix 27: 別承認（grant 専用の承認記録）を要求する2キー。
# `run9_identity_anchor` はこの集合に含めない — attestation 自体が
# anchor grant の根拠であり、追加の承認記録は要求しない
# （DESIGN_RUN9_REVISION_0.2.md 194-203行）。
_RIGHTS_MANIFEST_USAGE_GRANTS_REQUIRING_SEPARATE_APPROVAL: FrozenSet[str] = frozenset(
    {"raw_audio_publication", "model_general_distribution"}
)


def _validate_rights_manifest_usage_grant_approval_record(grant_key: str, approval: Any) -> None:
    """`usage_grants.<grant_key>_approval` の形状を検証する（Fix 19）:
    `approved_at`（UTC ISO 8601 タイムスタンプ）+ `approval_statement`
    （非空 str）の2キー閉集合。`_validate_rights_manifest_voice_identity_
    attestation()` の attested 形態フィールドと同じタイムスタンプ正規表現
    （`_RIGHTS_MANIFEST_UTC_TIMESTAMP_RE`）を再利用する。
    """
    path = f"rights manifest.voice_identity_rights.usage_grants.{grant_key}{_RIGHTS_MANIFEST_USAGE_GRANT_APPROVAL_SUFFIX}"
    if not isinstance(approval, dict):
        raise Run9ValidationError(f"{path} must be an object, got {type(approval).__name__}")
    unknown = set(approval.keys()) - _RIGHTS_MANIFEST_USAGE_GRANT_APPROVAL_KEYS
    if unknown:
        raise Run9ValidationError(f"{path} has unknown key(s): {sorted(unknown)}")
    missing = _RIGHTS_MANIFEST_USAGE_GRANT_APPROVAL_KEYS - set(approval.keys())
    if missing:
        raise Run9ValidationError(f"{path} missing required key(s): {sorted(missing)}")
    approved_at = approval["approved_at"]
    if (
        not isinstance(approved_at, str)
        or not _RIGHTS_MANIFEST_UTC_TIMESTAMP_RE.match(approved_at)
        or not _is_real_utc_timestamp(approved_at)
    ):
        raise Run9ValidationError(
            f"{path}.approved_at must be a UTC ISO 8601 timestamp denoting a real "
            f"calendar date/time (e.g. '2026-08-25T00:00:00Z'), got {approved_at!r}"
        )
    approval_statement = approval["approval_statement"]
    if not isinstance(approval_statement, str) or not approval_statement.strip():
        raise Run9ValidationError(
            f"{path}.approval_statement must be a non-empty string, got {approval_statement!r}"
        )


def _validate_rights_manifest_usage_grants(layer: Mapping[str, Any]) -> None:
    """`voice_identity_rights.usage_grants` の値語彙 + `granted` 遷移の
    前提条件を検証する（Fix 19、grant 別規則は Fix 27 で訂正）。呼び出し元
    `validate_rights_manifest_four_layer()` が本関数より先に
    `_validate_rights_manifest_voice_identity_attestation(layer)` を実行
    済みであることを前提にする（`layer["attestation"]["attested"]` が
    型検証済みの bool であることに依拠する）。

    grant 別の `granted` 遷移前提条件（Fix 27, PR #319 第14巡, P2, 採用 —
    第9巡裁定 Fix 19 の「3キー一律」を訂正。DESIGN_RUN9_REVISION_0.2.md
    194-203行が正）:
    - `raw_audio_publication` / `model_general_distribution`
      （`_RIGHTS_MANIFEST_USAGE_GRANTS_REQUIRING_SEPARATE_APPROVAL`）:
      ①attested 形態 ②grant 専用の承認記録（`<grant>_approval`）の
      両方が必須（rev 0.2 改訂4の「別承認」規定）。
    - `run9_identity_anchor`: ①attested 形態のみが必須。承認記録
      （`run9_identity_anchor_approval`）は要求しない
      （attestation 自体が anchor grant の根拠）。ただし付与されている
      場合は形状を検証する（未検証のゴミデータを通さない）。
    """
    path = "rights manifest.voice_identity_rights.usage_grants"
    usage_grants = layer.get("usage_grants")
    if not isinstance(usage_grants, dict):
        raise Run9ValidationError(f"{path} must be an object, got {type(usage_grants).__name__}")

    approval_keys = {
        f"{grant_key}{_RIGHTS_MANIFEST_USAGE_GRANT_APPROVAL_SUFFIX}"
        for grant_key in _RIGHTS_MANIFEST_USAGE_GRANTS_KEYS
    }
    allowed_keys = _RIGHTS_MANIFEST_USAGE_GRANTS_KEYS | approval_keys
    unknown = set(usage_grants.keys()) - allowed_keys
    if unknown:
        raise Run9ValidationError(f"{path} has unknown key(s): {sorted(unknown)}")
    missing = _RIGHTS_MANIFEST_USAGE_GRANTS_KEYS - set(usage_grants.keys())
    if missing:
        raise Run9ValidationError(f"{path} missing required key(s): {sorted(missing)}")

    attestation = layer["attestation"]
    attested = attestation["attested"]

    for grant_key in sorted(_RIGHTS_MANIFEST_USAGE_GRANTS_KEYS):
        value = usage_grants[grant_key]
        if value not in _RIGHTS_MANIFEST_USAGE_GRANT_VALUES:
            raise Run9ValidationError(
                f"{path}.{grant_key} must be one of "
                f"{sorted(_RIGHTS_MANIFEST_USAGE_GRANT_VALUES)}, got {value!r}"
            )
        approval_key = f"{grant_key}{_RIGHTS_MANIFEST_USAGE_GRANT_APPROVAL_SUFFIX}"
        requires_separate_approval = (
            grant_key in _RIGHTS_MANIFEST_USAGE_GRANTS_REQUIRING_SEPARATE_APPROVAL
        )
        if value == _RIGHTS_MANIFEST_USAGE_GRANT_GRANTED:
            if not attested:
                raise Run9ValidationError(
                    f"{path}.{grant_key} is "
                    f"{_RIGHTS_MANIFEST_USAGE_GRANT_GRANTED!r} but "
                    "rights manifest.voice_identity_rights.attestation is not in attested form "
                    "(attested=true) — a usage grant requires User attestation to have "
                    "completed first"
                )
            if requires_separate_approval and approval_key not in usage_grants:
                raise Run9ValidationError(
                    f"{path}.{grant_key} is {_RIGHTS_MANIFEST_USAGE_GRANT_GRANTED!r} but is "
                    f"missing its separate approval record {path}.{approval_key} — "
                    "raw_audio_publication/model_general_distribution each require independent "
                    "approval evidence and are never auto-escalated from one another "
                    "(DESIGN_RUN9_REVISION_0.2.md 改訂4 194-199行). run9_identity_anchor is "
                    "exempt from this requirement (改訂4 194-203行 — attestation itself is the "
                    "anchor grant's basis; Fix 27 corrects Fix 19's uniform 3-key rule)"
                )
            if approval_key in usage_grants:
                _validate_rights_manifest_usage_grant_approval_record(grant_key, usage_grants[approval_key])
        elif approval_key in usage_grants:
            raise Run9ValidationError(
                f"{path}.{approval_key} must not be present while {path}.{grant_key} is "
                f"{_RIGHTS_MANIFEST_USAGE_GRANT_NOT_GRANTED!r} (an approval record only makes "
                "sense once the grant has actually transitioned to granted)"
            )


def _validate_rights_manifest_layer_status_value(layer_name: str, field_name: str, value: Any) -> None:
    """層直下の rights_class/consent_status 値の語彙検証（Fix 5）。

    非空文字列であることをまず強制する（削除・null 化・空文字はここで
    拒否 — permission フィールドの必須化）。値が予約済み裸トークン
    （`PENDING_USER_ATTESTATION` / `UNRESOLVED_EXTERNAL`）のいずれかに
    一致する場合のみ、層の主体種別（`_RIGHTS_MANIFEST_LAYER_FIELD_KIND`）
    と整合しているかを検査する——`_validate_rights_provenance_block()` が
    provenance ブロック内の角括弧付き placeholder に対して行う誤用拒否
    ロジックを、角括弧なしの層レベル裸トークン規約へ同型で拡張したもの
    （Fix 6 の張り替え後、この誤用拒否が全欄で整合することの機械的保証）。
    それ以外の非空文字列（recording_master_rights.consent_status の
    `LICENSE_CONFIRMED_USAGE_SCOPE_PENDING_TOOLING_REVIEW` のような具体的
    記述値）は自由記述として許容する。
    """
    path = f"rights manifest.{layer_name}.{field_name}"
    if not isinstance(value, str) or not value.strip():
        raise Run9ValidationError(f"{path} must be a non-empty string, got {value!r}")
    kind = _RIGHTS_MANIFEST_LAYER_FIELD_KIND[layer_name]
    # Codex bot レビュー PR #319 第16巡指摘, Fix 29（P2, 採用）: 層レベル
    # status は角括弧なしの裸トークン規約であり、nested provenance の
    # 角括弧綴り（`<PENDING_USER_ATTESTATION>` / `<UNRESOLVED_EXTERNAL>`）を
    # status 欄へ持ち込むと上記の裸トークン等値検査をすべてすり抜けて
    # 「具体的な自由記述値」として受理されてしまう——外部層が User
    # attestation 経路へ再入する、または未解決なのに解決済みの具体値に
    # 見える、の両誤導を塞ぐため、角括弧綴りの予約トークンは層 status
    # 欄では一律拒否する（正しい綴りは裸トークン）。
    stripped = value.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        inner = stripped[1:-1]
        if inner in (
            _RIGHTS_MANIFEST_STATUS_PENDING_USER_ATTESTATION,
            _RIGHTS_MANIFEST_STATUS_UNRESOLVED_EXTERNAL,
            _RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE,
        ):
            raise Run9ValidationError(
                f"{path} uses bracketed sentinel spelling {value!r}; layer-level "
                "status fields use the bare-token convention — write the token "
                "without angle brackets (and only where the layer's subject kind "
                "permits it)"
            )
    if (
        value == _RIGHTS_MANIFEST_STATUS_PENDING_USER_ATTESTATION
        and kind != _RIGHTS_MANIFEST_FIELD_KIND_USER
    ):
        raise Run9ValidationError(
            f"{path} is an external-fact layer; "
            f"{_RIGHTS_MANIFEST_STATUS_PENDING_USER_ATTESTATION!r} is reserved for "
            f"User-attributable layers — use {_RIGHTS_MANIFEST_STATUS_UNRESOLVED_EXTERNAL!r} instead"
        )
    if (
        value == _RIGHTS_MANIFEST_STATUS_UNRESOLVED_EXTERNAL
        and kind != _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL
    ):
        raise Run9ValidationError(
            f"{path} is a User-attributable layer; "
            f"{_RIGHTS_MANIFEST_STATUS_UNRESOLVED_EXTERNAL!r} is reserved for "
            f"external-fact layers — use {_RIGHTS_MANIFEST_STATUS_PENDING_USER_ATTESTATION!r} instead"
        )
    # Codex bot レビュー PR #319 第12巡指摘, Fix 25（P2, 採用）: Fix 24 が
    # `USER_ATTESTED_OWN_VOICE` を voice_identity_rights 層の User-donor
    # attestation 完了を表す正確な意味として確立した以上、その語彙を
    # 外部第三者層（performance_rights/composition_rights/
    # recording_master_rights = kind external）へ手編集で混入させ「User
    # attestation 済み」を偽装する経路は対称漏れとして塞ぐ——未解決
    # provenance と並存したまま validate を通過させない。
    # recording_master_rights を含む自由記述の具体値（このトークンと
    # 一致しない値）の既存受理には影響しない。
    if (
        value == _RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE
        and kind != _RIGHTS_MANIFEST_FIELD_KIND_USER
    ):
        raise Run9ValidationError(
            f"{path} is an external-fact layer; "
            f"{_RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE!r} is reserved for "
            "voice_identity_rights layer User-donor attestation — for this external layer, use "
            f"{_RIGHTS_MANIFEST_STATUS_UNRESOLVED_EXTERNAL!r} for an unresolved state or a concrete "
            "external rights description for a resolved one"
        )


# 層ごとに必須の provenance ブロック（DESIGN_RUN9_REVISION_0.4.md 「4層構造
# への再編」対応表: performance_rights→performance_author /
# composition_rights→composition / recording_master_rights→voice_source+
# synthesis）。voice_identity_rights は donor ledger 転記であり provenance
# 節自体を持たない別構造のため、本辞書には含めない（validate 側でも別扱い）。
_RIGHTS_MANIFEST_PROVENANCE_LAYER_BLOCKS: Dict[str, FrozenSet[str]] = {
    "performance_rights": frozenset({"performance_author"}),
    "composition_rights": frozenset({"composition"}),
    "recording_master_rights": frozenset({"voice_source", "synthesis"}),
}

_RIGHTS_MANIFEST_NOT_APPLICABLE = "not_applicable"
# `not_applicable` を許すフィールドの宣言的 allowlist（Codex bot レビュー
# PR #319 第6巡指摘, Fix 12, P2, 採用）: DESIGN_RUN9_REVISION_0.4.md
# 「provenance の実値充填」表が構造的欠落（値未確定ではなく主体そのものが
# 存在しない）として `not_applicable` を許すのは
# performance_author.performance_editor と synthesis.engine /
# synthesis.voicebank の3欄のみ——いずれも「PJS は自然録音コーパスで
# UTAU 型の別調声レイヤー/合成エンジンを経由しない」という同一の構造的
# 不在根拠を共有する。voice_source.owner / performance_author.performer /
# composition.composer / composition.lyricist のような必須権利保有者欄へ
# `not_applicable` を通すと、未解決の owner を消去し将来の R9-G1 tooling
# に NO_UNKNOWN_RIGHTS_HOLDER を偽成立させ得るため、allowlist 外は
# fail-closed で拒否する（未解決値は `<UNRESOLVED_EXTERNAL>`／
# `<PENDING_USER_ATTESTATION>` を使わせる）。
_RIGHTS_MANIFEST_NOT_APPLICABLE_ALLOWLIST: FrozenSet[Tuple[str, str]] = frozenset(
    {
        ("performance_author", "performance_editor"),
        ("synthesis", "engine"),
        ("synthesis", "voicebank"),
    }
)
# 2026-08-25 User 追加裁定②: 外部不明値と User 帰属未確定値を同一
# placeholder `<PENDING_USER_ATTESTATION>` で表していた旧実装は誤り
# だった——User が attest できるのは自身の許諾・裁定のみであり、PJS の
# performer/composer 等の第三者事実には及ばない。語彙を分離する:
_RIGHTS_MANIFEST_PENDING_USER_ATTESTATION = "<PENDING_USER_ATTESTATION>"
_RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL = "<UNRESOLVED_EXTERNAL>"


def _validate_rights_provenance_block(layer_name: str, block_name: str, block: Any) -> None:
    """provenance 内の1ブロック（voice_source/performance_author/synthesis/
    composition のいずれか）が閉集合形状 + 値語彙規約を満たすことを検証する。

    値語彙（2026-08-25 User 追加裁定②）: 非空 str（自由記述文字列）、
    `<PENDING_USER_ATTESTATION>`（**User 帰属欄のみ** — User 自身が
    attest すべき対象の未確定値）、`<UNRESOLVED_EXTERNAL>`（**外部事実欄
    のみ** — 第三者の事実で repo 内に確認できる記録が無い未解決値）、
    または `not_applicable`（`note` に非空の理由説明を必須で伴う — 値と
    理由を切り離さない、捏造禁止規律。かつ
    `_RIGHTS_MANIFEST_NOT_APPLICABLE_ALLOWLIST` 記載の3欄
    （performance_author.performance_editor / synthesis.engine /
    synthesis.voicebank）以外では fail-closed で拒否する — Codex bot
    レビュー PR #319 第6巡指摘, Fix 12, P2, 採用。値未確定ではなく主体
    そのものが構造的に存在しない場合のみ許す語彙であり、owner/performer/
    composer/lyricist のような必須権利保有者欄で使うと未解決を消去し
    NO_UNKNOWN_RIGHTS_HOLDER を偽成立させ得る）。フィールドの種別と異なる
    placeholder を使った場合は拒否する（例: 外部事実欄に
    `<PENDING_USER_ATTESTATION>` を使うのは誤用 — 旧
    `performance_author.performer`/`composition.composer`/`lyricist` が
    この誤用の実例だった）。
    """
    field_kinds = _RIGHTS_MANIFEST_PROVENANCE_BLOCK_VALUE_KEYS[block_name]
    value_keys = frozenset(field_kinds.keys())
    path = f"rights manifest.{layer_name}.provenance.{block_name}"
    if not isinstance(block, dict):
        raise Run9ValidationError(f"{path} must be an object, got {type(block).__name__}")
    allowed_keys = value_keys | {"note"}
    unknown = set(block.keys()) - allowed_keys
    if unknown:
        raise Run9ValidationError(f"{path} has unknown key(s): {sorted(unknown)}")
    missing = value_keys - set(block.keys())
    if missing:
        raise Run9ValidationError(f"{path} missing required key(s): {sorted(missing)}")
    uses_not_applicable = False
    for key in sorted(value_keys):
        value = block[key]
        if not isinstance(value, str) or not value.strip():
            raise Run9ValidationError(f"{path}.{key} must be a non-empty string, got {value!r}")
        if value == _RIGHTS_MANIFEST_NOT_APPLICABLE:
            if (block_name, key) not in _RIGHTS_MANIFEST_NOT_APPLICABLE_ALLOWLIST:
                raise Run9ValidationError(
                    f"{path}.{key} does not permit 'not_applicable' — only "
                    "performance_author.performance_editor / synthesis.engine / "
                    "synthesis.voicebank may be structurally absent; for an unresolved "
                    f"value use {_RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL!r} (external-fact "
                    f"field) or {_RIGHTS_MANIFEST_PENDING_USER_ATTESTATION!r} "
                    "(User-attributable field) instead"
                )
            uses_not_applicable = True
            continue
        kind = field_kinds[key]
        if value == _RIGHTS_MANIFEST_PENDING_USER_ATTESTATION and kind != _RIGHTS_MANIFEST_FIELD_KIND_USER:
            raise Run9ValidationError(
                f"{path}.{key} is an external-fact field; "
                f"{_RIGHTS_MANIFEST_PENDING_USER_ATTESTATION!r} is reserved for "
                f"User-attributable fields — use {_RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL!r} instead"
            )
        if value == _RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL and kind != _RIGHTS_MANIFEST_FIELD_KIND_EXTERNAL:
            raise Run9ValidationError(
                f"{path}.{key} is a User-attributable field; "
                f"{_RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL!r} is reserved for "
                f"external-fact fields — use {_RIGHTS_MANIFEST_PENDING_USER_ATTESTATION!r} instead"
            )
        # Codex bot レビュー PR #319 第13巡指摘, Fix 26（P2, 採用）: Fix 25 は
        # 層直下の rights_class/consent_status（裸トークン）への
        # `USER_ATTESTED_OWN_VOICE` 混入は塞いだが、本ブロック（provenance
        # 内の performance_author.performer / composition.composer /
        # composition.lyricist / voice_source.owner 等、角括弧なし自由記述
        # を受理する具体値検証パス）は素通りしていた——第三者 author/権利者
        # 欄を User-donor 完了トークンで置換したまま validate を通過でき、
        # R9-G1 が消費する provenance を汚染し得る。Fix 25 と同型の
        # fail-closed 拒否を対称に適用する。
        if (
            value == _RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE
            and kind != _RIGHTS_MANIFEST_FIELD_KIND_USER
        ):
            raise Run9ValidationError(
                f"{path}.{key} is an external-fact field; "
                f"{_RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE!r} is reserved for "
                "voice_identity_rights layer User-donor attestation — for this external field, use "
                f"{_RIGHTS_MANIFEST_UNRESOLVED_EXTERNAL!r} for an unresolved state or a concrete "
                "external rights description for a resolved one"
            )
    if uses_not_applicable:
        note = block.get("note")
        if not isinstance(note, str) or not note.strip():
            raise Run9ValidationError(
                f"{path} uses 'not_applicable' but is missing a non-empty 'note' reason"
            )


def extract_voice_identity_rights_layer(four_layer_rights_manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """rev 0.4 の4層 rights_manifest（schema `run9-rights-manifest/2.0`）
    から `voice_identity_rights` 層を取り出し、
    `verify_rights_manifest_against_ledger()` が受理する旧 schema
    `run9-user-donor-rights/1.0` 相当のフラット構造へ変換する。

    `verify_rights_manifest_against_ledger()` 自体は書き換えない —
    本関数はその手前に立つアダプタであり、`.get("schema")`/`.get("entries")`
    のみを読む同関数の契約に合わせて `schema_legacy` を `schema` へ
    読み替える。返り値の他のキー（`entries`/`usage_grants`/`attestation`
    等）は layer の内容をそのまま透過する（コピーであり、呼び出し元が
    書き換えても入力 `four_layer_rights_manifest` には影響しない）。

    抽出は `validate_rights_manifest_four_layer()` による4層全体検証を
    内包する（fail-closed）——`performance_rights`/`composition_rights`/
    `recording_master_rights` のいずれかが欠落・不正な manifest からは
    `voice_identity_rights` 単独が構造的に妥当でも抽出そのものを拒否する。
    RUN9_CONTRACT.yaml documented フロー「層を抽出して legacy verifier へ
    渡す」が他の必須層を静かに失ったまま donor rights だけ検証できて
    しまう抜け道を塞ぐ（Codex bot レビュー PR #319 第7巡指摘 Fix 14、P2、
    採用）。
    """
    if not isinstance(four_layer_rights_manifest, dict):
        raise Run9ValidationError(
            "four-layer rights manifest must be an object, got "
            f"{type(four_layer_rights_manifest).__name__}"
        )
    validate_rights_manifest_four_layer(four_layer_rights_manifest)
    top_schema = four_layer_rights_manifest.get("schema")
    if top_schema != SCHEMA_RIGHTS_MANIFEST_FOUR_LAYER:
        raise Run9ValidationError(
            f"four-layer rights manifest schema must be exactly "
            f"{SCHEMA_RIGHTS_MANIFEST_FOUR_LAYER!r}, got {top_schema!r}"
        )
    layer = four_layer_rights_manifest.get("voice_identity_rights")
    if not isinstance(layer, dict):
        raise Run9ValidationError(
            f"voice_identity_rights layer must be an object, got {type(layer).__name__}"
        )
    legacy_schema = layer.get("schema_legacy")
    if legacy_schema != "run9-user-donor-rights/1.0":
        raise Run9ValidationError(
            "voice_identity_rights.schema_legacy must be exactly 'run9-user-donor-rights/1.0', "
            f"got {legacy_schema!r}"
        )
    flat = copy.deepcopy(dict(layer))
    flat.pop("schema_legacy")
    flat["schema"] = legacy_schema
    return flat


# Codex bot レビュー PR #320 第2巡指摘（P1, 採用, Fix 3）: `extract_voice_
# identity_rights_layer()` は voice_identity_rights 層**全体**（Fix 1 の
# binding scope 是正後の対象）を anchor 束縛値にしていたが、その payload
# には `usage_grants` と `usage_grants_note` も含まれる。`raw_audio_
# publication` / `model_general_distribution` は rev 0.2 改訂4が定める
# 設計上正規の別承認により not_granted → granted へ遷移し得るため、この
# 遷移が起きるたびに anchor（延いては genome_id）が動く——「据え置けば
# stale pin、追随すれば不要な genome_id 変動」という Fix 1 で解消した
# はずの二律背反が形を変えて再発する。anchor 束縛の対象を「attest 後の
# あらゆる設計上正規の遷移に対して不変な部分集合」（本関数が返す
# projection）へさらに再限定する。
_RIGHTS_MANIFEST_ATTESTATION_PROJECTION_SCHEMA = "run9-identity-attestation-projection/1.0"

# projection が voice_identity_rights 層から逐語コピーするキー
# （donor_ledger_source/donor_ledger_schema/transcribed_at = 転記
# provenance、entries = UC-001..017、rights_class/consent_status/
# attestation = 宣誓事実そのもの）。schema/source_layer の2キーは
# projection 自身が新設するリテラルで、層からのコピーではない。
_RIGHTS_MANIFEST_ATTESTATION_PROJECTION_COPIED_KEYS: Tuple[str, ...] = (
    "donor_ledger_source",
    "donor_ledger_schema",
    "transcribed_at",
    "entries",
    "rights_class",
    "consent_status",
    "attestation",
)


def extract_user_identity_attestation_projection(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """4層 rights_manifest（schema `run9-rights-manifest/2.0`）から、User
    donor identity の anchor pin 専用に「attest 後の設計上正規のあらゆる
    遷移に対して不変な」部分集合（identity-attestation projection）を
    抽出する（Fix 3）。`domains/identity_domain_run9_v1.json anchor_hashes.
    user` が束縛するのは本関数の返り値の正規形 sha256 であり、
    `extract_voice_identity_rights_layer()`（voice_identity_rights 層
    全体）の返り値ではない——後者は
    `verify_rights_manifest_against_ledger()` 向けの汎用アダプタとして
    従来どおり残る（削除・改変しない。anchor 束縛の役割だけが本関数へ
    移る）。

    内部で `validate_rights_manifest_four_layer()`（既存の4層全体構造
    検証、`extract_voice_identity_rights_layer()` と同じ fail-closed
    方式）を先に実行する。さらに2つの fail-closed 前提条件を課す:
      (i) `attestation` が attested 形態（`attested=True`）であること
          ——本 projection は anchor pin 専用であり、pending 形態
          （`attested=False`）の hash が anchor 候補として見える経路を
          ここで構造的に閉じる。
      (ii) `usage_grants.run9_identity_anchor == "granted"` であること
          ——Codex bot レビュー PR #320 第4巡指摘（P1, 採用, Fix 6）:
          `usage_grants` は除外理由1（下記）により projection の hash
          payload には含めないが、この grant を User が取消し
          （`"granted"` → `"not_granted"`、attestation 自体は歴史的
          記録として保持したまま）ても、hash を不変に保つ設計原則
          （不変性の設計原則、下記）と衝突しない形で取消を検知しなければ
          ならない。取消状態の manifest から anchor-eligible hash を
          生成・再検証させないよう、抽出そのものを拒否する——「hash は
          宣誓事実のみを表し、許諾状態（取消し得る）はこのゲートで検証
          する」という層分離が本関数の設計原則である（hash 側を可変
          grant の値へ連動させて repin させる旧方式（Fix 3 以前）へは
          戻さない）。いずれかが不成立なら `Run9ValidationError`
          （`ValueError` サブクラス）で拒否する。
    どちらの前提条件も違反すると本関数は projection を返さない——
    projection の**中身**（返り値の閉じたキー集合）に `usage_grants` を
    含めないことと、抽出**可否**の判定に `usage_grants.run9_identity_
    anchor` を使うことは矛盾しない（前者は hash 対象、後者は抽出の
    gate 条件であり、別の軸）。

    返り値は以下の**閉じたキー集合のみ**を持つ dict（他キー混入禁止）:
      - `schema`: `"run9-identity-attestation-projection/1.0"`
        （projection 自身の新設リテラル。self-describing であることに
        加え、`voice_identity_rights.schema_legacy`
        （`run9-user-donor-rights/1.0`）や `extract_voice_identity_rights_
        layer()` の返り値（`schema` = 同 legacy 値）とはハッシュ対象の
        キー集合・意味が異なるため、schema 文字列自体でドメイン分離する）
      - `source_layer`: `"voice_identity_rights"`
      - `donor_ledger_source` / `donor_ledger_schema` / `transcribed_at`
        （層から逐語コピー — donor ledger からの転記であることを示す
        provenance）
      - `entries`（層から逐語コピー — UC-001..UC-017 の17件）
      - `rights_class` / `consent_status`（層から逐語コピー）
      - `attestation`（層から逐語コピー — attested/attested_by/
        attested_at/statement の4キー、上記ガードにより常に attested
        形態）

    **意図的に除外するもの**（除外理由）:
      1. `usage_grants` / `usage_grants_note` / 任意の `<grant>_approval`
         ブロック — `raw_audio_publication` / `model_general_distribution`
         は「User attest 完了後、別承認により not_granted → granted へ
         遷移する」という rev 0.2 改訂4（DESIGN_RUN9_REVISION_0.2.md
         194-199行）が定める設計上正規の可変状態であり、宣誓事実
         （entries/attestation/両 status）とは別の遷移軸を持つ。
         `run9_identity_anchor` grant も同じ理由で除外する——Fix 27（PR
         #319 第14巡）により同 grant の唯一の根拠は
         `attestation.attested=True` 自体であり（別途の承認記録は要求
         されない）、projection に既に含む `attestation` ブロックを超える
         情報を grant 自体は持たない派生状態にすぎない。含めれば
         `usage_grants` の値が動くたびに repin が必要になる編集面が
         1つ増えるだけで、projection の不変性を薄める。
         **除外はゲート化とセット**（Fix 6, PR #320 第4巡指摘, P1, 採用）:
         `run9_identity_anchor` を hash payload から除外する一方で、
         取消（`"granted"` → `"not_granted"`）は hash ではなく本関数の
         前提条件 (ii)（下記）で fail-closed に検知する——「hash に
         戻して repin させる」のではなく「抽出そのものをゲートする」。
         これにより hash 不変性（Fix 3 の設計原則）を保ったまま、取消の
         事実を anchor pin 生成/再検証の経路で確実に効かせる。
      2. `role` / `note` / `usage_grants_note` / `binding_note` の散文
         ドキュメント欄 — レビューでの文言明確化が宣誓事実を変えずに
         正当に進化する欄であり、特に `binding_note` は束縛方式自体の
         記述という自己参照になる（含めると binding 文書の明確化の
         たびに repin が強制される——実際に Fix 1 で `binding_note` の
         追記が repin を要した前例がある。Fix 3 でこの自己参照を解消
         する）。
      3. `schema_legacy`（`extract_voice_identity_rights_layer()` は
         これを `schema` へ読み替えて透過するが、本 projection は
         projection 自身の新設 schema リテラル（上記）で置き換える —
         legacy アダプタの都合を anchor pin 側へ持ち込まない）。

    不変性の設計原則: projection に含まれる全フィールドは attest 後の
    設計上正規のあらゆる遷移（grant 別承認・散文編集・外部3層
    （performance_rights/composition_rights/recording_master_rights）の
    解決）に対して不変。anchor を動かせるのは宣誓事実そのもの
    （entries / attestation / 両 status）の変更のみである。
    """
    if not isinstance(manifest, dict):
        raise Run9ValidationError(
            "four-layer rights manifest must be an object, got "
            f"{type(manifest).__name__}"
        )
    validate_rights_manifest_four_layer(manifest)
    layer = manifest.get("voice_identity_rights")
    if not isinstance(layer, dict):
        raise Run9ValidationError(
            f"voice_identity_rights layer must be an object, got {type(layer).__name__}"
        )
    attestation = layer.get("attestation")
    attested = attestation.get("attested") if isinstance(attestation, dict) else attestation
    if attested is not True:
        raise Run9ValidationError(
            "extract_user_identity_attestation_projection() requires "
            "voice_identity_rights.attestation.attested == True — this projection is "
            "anchor-pin-only and must not expose a pending-form hash as an anchor "
            f"candidate (Fix 3), got attestation.attested={attested!r}"
        )
    usage_grants = layer.get("usage_grants")
    anchor_grant = (
        usage_grants.get("run9_identity_anchor") if isinstance(usage_grants, dict) else usage_grants
    )
    if anchor_grant != _RIGHTS_MANIFEST_USAGE_GRANT_GRANTED:
        raise Run9ValidationError(
            "extract_user_identity_attestation_projection() requires "
            "voice_identity_rights.usage_grants.run9_identity_anchor == 'granted' — the User "
            "has revoked the anchor-use grant (attestation itself is retained as a historical "
            "record, but the grant that authorizes using it as the RUN9 identity anchor is "
            "not_granted). This projection is anchor-pin-generation/verification-only; it must "
            "not produce an anchor-eligible hash for a manifest whose anchor-use grant is "
            f"revoked (Fix 6, PR #320 4th round), got usage_grants.run9_identity_anchor="
            f"{anchor_grant!r}"
        )
    projection: Dict[str, Any] = {
        "schema": _RIGHTS_MANIFEST_ATTESTATION_PROJECTION_SCHEMA,
        "source_layer": "voice_identity_rights",
    }
    for key in _RIGHTS_MANIFEST_ATTESTATION_PROJECTION_COPIED_KEYS:
        projection[key] = copy.deepcopy(layer[key])
    return projection


def validate_rights_manifest_four_layer(data: Mapping[str, Any]) -> None:
    """4層 rights_manifest（schema `run9-rights-manifest/2.0`）の構造を
    検証する。実体（provenance の個別値が事実として正しいか）は
    R9-G1 tooling の職務のまま——本関数は構造・閉じたキー集合・原則3式・
    禁止文言・4層すべての存在に加え、layer ごとの provenance ネスト
    ブロック形状（`_RIGHTS_MANIFEST_PROVENANCE_LAYER_BLOCKS`/
    `_validate_rights_provenance_block()` — voice_source{owner,source_id}/
    performance_author{performer,performance_editor}/
    synthesis{engine,voicebank}/composition{composer,lyricist}の閉集合と
    値語彙）、さらに `recording_master_rights.interpretations`（CC BY-SA
    4.0 の share-alike 義務が合成出力へ及ぶかという法解釈を、事実である
    `license` 節から分離する節 — 2026-08-25 User 追加裁定②）の存在・形状
    まで検証する（`validate_branch_write_policy_manifest()` 等と同じ
    「構造のみ」の境界宣言）。`voice_identity_rights.attestation` は
    pending（`attested=False` + signer/timestamp/statement すべて `None`）/
    attested（`attested=True` + signer/UTC ISO 8601 timestamp/非空
    statement すべて充足）の二形態のみを許可し、層の rights_class/
    consent_status（PENDING_USER_ATTESTATION か否か）との整合も検証する
    （`_validate_rights_manifest_voice_identity_attestation()` — Codex bot
    レビュー PR #319 第8巡指摘、Fix 16、P2、採用）。fail-closed（未知キー
    拒否・欠落キーのデフォルト補完なし・`provenance: {}` やブロック欠落を
    素通りさせない — Codex bot レビュー PR #319 第1巡指摘2、P2、採用）。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(f"rights manifest must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _RIGHTS_MANIFEST_FOUR_LAYER_TOP_KEYS
    if unknown:
        raise Run9ValidationError(f"rights manifest has unknown top-level key(s): {sorted(unknown)}")
    missing = _RIGHTS_MANIFEST_FOUR_LAYER_TOP_KEYS - {"history"} - set(data.keys())
    if missing:
        raise Run9ValidationError(f"rights manifest missing required top-level key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_RIGHTS_MANIFEST_FOUR_LAYER:
        raise Run9ValidationError(
            f"rights manifest schema must be exactly {SCHEMA_RIGHTS_MANIFEST_FOUR_LAYER!r}, "
            f"got {schema!r}"
        )

    principles = data["principles"]
    if not isinstance(principles, dict) or "statements" not in principles:
        raise Run9ValidationError("rights manifest.principles must be an object with a 'statements' key")
    statements = principles["statements"]
    if not isinstance(statements, list) or tuple(statements) != RIGHTS_MANIFEST_PRINCIPLES:
        raise Run9ValidationError(
            f"rights manifest.principles.statements must be exactly {list(RIGHTS_MANIFEST_PRINCIPLES)} "
            f"(order included), got {statements!r}"
        )

    auto_interp = data["auto_interpretation_prohibited"]
    if not isinstance(auto_interp, str) or not auto_interp.strip():
        raise Run9ValidationError(
            "rights manifest.auto_interpretation_prohibited must be a non-empty string"
        )

    hard_gate = data["hard_gate"]
    if not isinstance(hard_gate, str) or not hard_gate.strip():
        raise Run9ValidationError("rights manifest.hard_gate must be a non-empty string")

    for layer_name in RIGHTS_MANIFEST_LAYER_NAMES:
        layer = data[layer_name]
        if not isinstance(layer, dict):
            raise Run9ValidationError(f"rights manifest.{layer_name} must be an object")
        # Fix 5（P2, 採用）: 層ごとの必須キー閉集合を強制する——欠落キー
        # （license/rights_class/consent_status 等の permission フィールド
        # の削除を含む）・未知キーのいずれも拒否する。role/provenance の
        # 個別存在チェック（このすぐ下）より前に走らせることで、
        # 「role だけ・provenance だけ揃っていれば他は素通り」という旧
        # 経路を構造的に閉じる。
        required_layer_keys = _RIGHTS_MANIFEST_LAYER_REQUIRED_KEYS[layer_name]
        missing_layer_keys = required_layer_keys - set(layer.keys())
        if missing_layer_keys:
            raise Run9ValidationError(
                f"rights manifest.{layer_name} missing required key(s): {sorted(missing_layer_keys)}"
            )
        extra_layer_keys = set(layer.keys()) - required_layer_keys
        if extra_layer_keys:
            raise Run9ValidationError(
                f"rights manifest.{layer_name} has unknown key(s): {sorted(extra_layer_keys)}"
            )
        if not isinstance(layer.get("role"), str) or not layer["role"].strip():
            raise Run9ValidationError(f"rights manifest.{layer_name}.role must be a non-empty string")
        # Fix 5（P2, 採用）+ Fix 6 の張り替え後の整合確認: rights_class/
        # consent_status は必須キーとして存在するだけでなく、値語彙も検証
        # する（既存の placeholder 語彙 — 層レベルの裸トークン規約 — + 自由
        # 記述の具体値のいずれかであることの確認、および誤用拒否）。
        _validate_rights_manifest_layer_status_value(layer_name, "rights_class", layer.get("rights_class"))
        _validate_rights_manifest_layer_status_value(
            layer_name, "consent_status", layer.get("consent_status")
        )
        if layer_name == "voice_identity_rights":
            # Fix 16（P2, 採用）: attestation の形状 + pending/attested 二形態の
            # 整合を検証する（`{}`/スカラー/signer 欠落の `{"attested": true}`
            # を素通りさせない）。usage_grants の granted 前提条件（Fix 19）が
            # attestation の attested 形態を参照するため、usage_grants の
            # 検証より先に実行する（attestation の形状が既に妥当であることを
            # 前提にできる）。
            _validate_rights_manifest_voice_identity_attestation(layer)
            # Fix 8（P2, 採用, 旧実装）: usage_grants は必須キーとして存在
            # するだけでは中身の形状を保証しない——`{}` やスカラー置換でも
            # 旧実装は素通りしていた。Fix 19（P2, 採用, Codex bot レビュー
            # PR #319 第9巡指摘）でさらに強化: 3キー閉集合 + 非空文字列
            # という Fix 8 の最小制約だけでは、値そのものを任意の文字列へ
            # 書き換えても（例 raw_audio_publication を素通りする
            # `"granted"` へ）attestation.attested=false のまま通ってしまう
            # ——rev 0.2 改訂4「raw 音源の公開・モデルの一般配布は別承認」を
            # 裏付ける証拠なしに公開/配布許可を手編集で成立させ得た。値語彙を
            # `{not_granted, granted}` の閉集合へ凍結し、granted への遷移は
            # ①attestation が attested 形態であること ②当該 grant の別承認
            # の証拠記録（承認日時 UTC ISO 8601 + 承認文言）が存在すること、
            # の両方を fail-closed で要求する。
            _validate_rights_manifest_usage_grants(layer)
        if layer_name != "voice_identity_rights":
            # performance_rights/composition_rights/recording_master_rights は
            # いずれも provenance 節を持つ（voice_identity_rights は donor
            # ledger 転記であり provenance 節を持たない別構造 — 混同しない）。
            provenance = layer.get("provenance")
            if not isinstance(provenance, dict):
                raise Run9ValidationError(
                    f"rights manifest.{layer_name}.provenance must be an object"
                )
            # Codex bot レビュー PR #319 第1巡指摘2（P2）採用: dict である
            # ことだけでなく、層ごとの必須ブロック（下記
            # _RIGHTS_MANIFEST_PROVENANCE_LAYER_BLOCKS）が揃っていること・
            # 未知ブロックが無いこと・各ブロック内部の形状が閉集合を満たす
            # ことまで検証する（`provenance: {}` を素通りさせない）。
            required_blocks = _RIGHTS_MANIFEST_PROVENANCE_LAYER_BLOCKS[layer_name]
            missing_blocks = required_blocks - set(provenance.keys())
            if missing_blocks:
                raise Run9ValidationError(
                    f"rights manifest.{layer_name}.provenance missing required block(s): "
                    f"{sorted(missing_blocks)}"
                )
            extra_blocks = set(provenance.keys()) - required_blocks
            if extra_blocks:
                raise Run9ValidationError(
                    f"rights manifest.{layer_name}.provenance has unknown block(s): "
                    f"{sorted(extra_blocks)}"
                )
            for block_name in sorted(required_blocks):
                _validate_rights_provenance_block(layer_name, block_name, provenance[block_name])

        if layer_name == "recording_master_rights":
            # Fix 8（P2, 採用）: license は必須キーとして存在するだけでは
            # 中身の形状を保証しない——`{}` や任意のスカラーへ置換しても
            # 旧実装は素通りしていた。value/scope/derivative_obligation/
            # source の4キー閉集合 + 非空文字列を強制する（CC BY-SA 4.0 の
            # ライセンス種別・適用範囲・義務・出典の欠落を検出可能にする）。
            _validate_rights_manifest_license_block(layer.get("license"))
            # Fix 8（P2, 採用）: corpus_pins も同型の未検証 object だった
            # （source archive pin / expanded corpus pin の2値が欠落・
            # スカラー化しても構造的に valid のまま通過していた）。
            _validate_rights_manifest_corpus_pins_block(layer.get("corpus_pins"))
            # 2026-08-25 User 追加裁定②: CC BY-SA 4.0 の share-alike 義務が
            # 合成出力へ及ぶかは事実でなく法解釈であり、`license`（事実）から
            # 分離した `interpretations` 節を必須とする。
            interpretations = layer.get("interpretations")
            if not isinstance(interpretations, dict) or not interpretations:
                raise Run9ValidationError(
                    "rights manifest.recording_master_rights.interpretations must be a "
                    "non-empty object (事実とライセンス適用の法解釈を分離する節が必須 — "
                    "2026-08-25 User 追加裁定②)"
                )
            for interp_key, interp_value in interpretations.items():
                # Fix 8（P2, 採用）: 旧実装は status/question/note の3キーが
                # 非空文字列であることのみを見ており、未知キーの混入
                # （実データが持つ `source` を含む）を拒否していなかった。
                # status/question/note/source の4キー閉集合を強制する。
                _validate_closed_string_object(
                    f"rights manifest.recording_master_rights.interpretations.{interp_key}",
                    interp_value,
                    _RIGHTS_MANIFEST_INTERPRETATION_ENTRY_KEYS,
                )

    performance_source = data["performance_rights"].get("performance_source")
    if not isinstance(performance_source, dict):
        raise Run9ValidationError("rights manifest.performance_rights.performance_source must be an object")
    if performance_source.get("id") != PERFORMANCE_SOURCE_ID:
        raise Run9ValidationError(
            f"rights manifest.performance_rights.performance_source.id must be "
            f"{PERFORMANCE_SOURCE_ID!r}, got {performance_source.get('id')!r}"
        )
    if performance_source.get("role") != PERFORMANCE_SOURCE_ROLE:
        raise Run9ValidationError(
            f"rights manifest.performance_rights.performance_source.role must be "
            f"{PERFORMANCE_SOURCE_ROLE!r}, got {performance_source.get('role')!r}"
        )

    # voice_identity_rights 層の内容自体（entries/usage_grants 等）は
    # `extract_voice_identity_rights_layer()` + 既存
    # `verify_rights_manifest_against_ledger()` が別途検証する
    # （層越境の実体検証は本関数の責務外）。


# ---------------------------------------------------------------------------
# rev 0.4（DESIGN_RUN9_REVISION_0.4.md、`RUN9_CONTRACT.yaml` 新設トップ
# レベル欄 `performance_source`）: 2026-08-25 User 追加裁定「確認メモ /
# RUN9 用語整理」指示2「置換でなく追加」の実装。既存 teacher 表記
# （v0.1・rev 0.2/0.3、byte-pin 不変の運用上の呼称）は書き換えず、本欄が
# Voice Source ≠ Performance Source ≠ Performance Author の分離を明示する
# 非所有注記の置き場所を担う。
# ---------------------------------------------------------------------------

_PERFORMANCE_SOURCE_BLOCK_KEYS: FrozenSet[str] = frozenset(
    {"id", "role", "rights_manifest_ref", "teacher_terminology_note"}
)


def validate_performance_source_block(data: Mapping[str, Any]) -> None:
    """`RUN9_CONTRACT.yaml` `performance_source` ブロックの構造を検証する。
    `id`/`role` は凍結値（`PERFORMANCE_SOURCE_ID`/`PERFORMANCE_SOURCE_ROLE`）
    に厳密一致し、`teacher_terminology_note` は非所有の趣旨（Voice 所有者
    を意味しない旨）を含む非空文字列でなければならない——文言の一言一句を
    強制せず、`TEACHER_TERMINOLOGY_NOTE` が満たす2要件（「Voice 所有者」
    という語と「Voice Source」「Performance Source」「Performance Author」
    の3語）の存在で判定する（2026-08-25 User 追加裁定 指示1）。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(f"performance_source must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _PERFORMANCE_SOURCE_BLOCK_KEYS
    if unknown:
        raise Run9ValidationError(f"performance_source has unknown key(s): {sorted(unknown)}")
    missing = _PERFORMANCE_SOURCE_BLOCK_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"performance_source missing required key(s): {sorted(missing)}")
    if data["id"] != PERFORMANCE_SOURCE_ID:
        raise Run9ValidationError(
            f"performance_source.id must be {PERFORMANCE_SOURCE_ID!r}, got {data['id']!r}"
        )
    if data["role"] != PERFORMANCE_SOURCE_ROLE:
        raise Run9ValidationError(
            f"performance_source.role must be {PERFORMANCE_SOURCE_ROLE!r}, got {data['role']!r}"
        )
    ref = data["rights_manifest_ref"]
    if not isinstance(ref, str) or not ref.strip():
        raise Run9ValidationError("performance_source.rights_manifest_ref must be a non-empty string")
    note = data["teacher_terminology_note"]
    if not isinstance(note, str) or not note.strip():
        raise Run9ValidationError("performance_source.teacher_terminology_note must be a non-empty string")
    if "Voice 所有者" not in note:
        raise Run9ValidationError(
            "performance_source.teacher_terminology_note must state that 'Teacher' does not mean "
            "the Voice owner (2026-08-25 User 追加裁定 指示1)"
        )
    for marker in ("Voice Source", "Performance Source", "Performance Author"):
        if marker not in note:
            raise Run9ValidationError(
                f"performance_source.teacher_terminology_note must reference {marker!r} — the "
                "Voice Source ≠ Performance Source ≠ Performance Author separation "
                "(2026-08-25 User 追加裁定 指示1)"
            )
