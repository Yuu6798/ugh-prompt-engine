# Event Roundtrip — R4-1 chord progression admission plan

R4 は、bpm / key / brightness のような制作パラメータではなく、曲を「その曲」
たらしめる事象レベル欄を往復させる。R4-1 では最初の事象欄としてコード進行を選び、
DD-D（どの欄に、どの観測量を、どの校正基準で対応付けるか）の解除条件だけを文書化する。
`CompositionScore` への欄追加、performer grip、比較実装は R4-2/R4-3 で行う。

## 三つ組テーブル

| 事象フィールド | 観測量（センサー） | 校正基準 | 有効帯域 |
|---|---|---|---|
| `chord_progression`（将来欄） | `compute_chord_events` → `PhysicalRPE.chord_events` | コード系列一致率（主）/ 移調不変形（別レポート） | major/minor 三和音のみ。7th/sus/dim/aug はセンサー盲 |

この表が R4 の DD-D 解除条件である。`chord_progression` はまだ
`CompositionScore` に存在しないため、本タスクでは「この欄を追加するなら何を計器として
読み、どの基準で往復保存を見るか」を固定するに留める。

## コードベース現状

### センサーは既存・決定論・major/minor 三和音限定

`src/svp_rpe/rpe/physical_features.py` には `compute_chord_events(y, sr)` が既にある。
その下流は `_chord_templates()` と `_classify_chroma_frames()` で、テンプレートは
major/minor triad だけを作る。`compute_chord_events` の docstring も
「deterministic」「dependency-free」「major/minor triads」に限定すると明記している。

出力型は `src/svp_rpe/rpe/models.py` の `ChordEvent` で、フィールドは
`chord` / `root` / `quality`（`Literal["major", "minor"]`）/ `start_sec` /
`end_sec` / `confidence`。`PhysicalRPE.chord_events` は
`List[ChordEvent] = Field(default_factory=list)` として既に載っている。

### grip ギャップ

`src/svp_rpe/perform/performer.py` は `MINOR_PROGRESSION` と `MAJOR_PROGRESSION` を
定数として持ち、`perform()` 内で `parse_key(score.physical.key)` から mode を読み、
`MINOR_PROGRESSION if mode == "minor" else MAJOR_PROGRESSION` を選ぶ。つまり現状の
performer は key から導出した固定進行を鳴らしており、独立したコード進行欄を読まない。

したがって R4 の実作業は、単にセンサーを探すことではなく、`CompositionScore` に
コード進行欄を追加し、performer がその欄を読む grip を作ることにある。

### score 欄と fixity は未対応

`src/svp_rpe/compose/models.py::CompositionScore` は `semantic` / `physical` /
`structure` / `rendering` / `fixity` を持つが、事象層を持たない。`PhysicalLayer` にも
コード進行欄は無い。

同じファイルの `validate_fixity_keys()` は `allowed = set(PhysicalLayer.model_fields)` を
使い、`fixity` のキーを `CompositionScore.physical` のフィールドだけに限定する。
`validate_fixity_matches_physical_values()` も `getattr(self.physical, field)` を見るため、
現状の fixity は物理層専用で、将来の事象欄をそのまま locked にできない。

## 校正基準

主指標はコード系列一致率とする。比較単位は `ChordEvent` 全体ではなく、
`{root, quality}` の列である。`start_sec` / `end_sec` / `confidence` はセンサー出力の
補助情報として保持するが、R4-3 の主比較ではコード名の系列保存を測る。

R4-3 の実装時は、たとえば source 系列 `S` と transcribed 系列 `T` を順序列として揃え、
一致した `{root, quality}` の数を `max(len(S), len(T))` で割る一致率を基本形にする。
厳密一致を合否の唯一条件にすると生成ゆらぎで計器が死ぬため、閾値は
`RoundtripField` を作る比較器側（roundtrip comparator / config / fixture）に置き、
Pydantic model には埋め込まない。

移調不変の「進行の形」は別レポートで併記する。理由は、絶対 root は `physical.key` と
独立に管理されるべきだからである。R3 以降の key 保存が別フィールドとして成立しているなら、
コード進行の主指標は絶対音の `{root, quality}` 系列を見てよい。移調不変形は
「同じ I-IV-V-I だが key がずれた」ケースを説明する診断情報であり、主判定に混ぜると
key と chord の誤差源が分離できなくなる。

有効帯域は major/minor 三和音だけである。7th / sus / dim / aug / slash chord /
テンション主体の和声を source が要求する場合、現行 `compute_chord_events` では読めない。
この場合は「生成器が悪い」ではなくセンサー盲帯として扱う。

## 入場試験への対応

`docs/score_centric_planning.md` §2.2 の入場試験は、事象欄にも同じ形で適用する。
新フィールドは、score → perform → extract → draft score の往復で保存される実測計画
または実測を持って初めて正規スキーマへ入る。

`src/svp_rpe/roundtrip/models.py::RoundtripField` は `source_value` /
`transcribed_value` を `Any` として持つため、事象欄ではここにコード列を入れる。
想定する値は次のような配列である。

```json
[
  {"root": "C", "quality": "major"},
  {"root": "F", "quality": "major"},
  {"root": "G", "quality": "major"},
  {"root": "C", "quality": "major"}
]
```

診断 4 値への対応は以下とする。

| 診断 | コード進行欄での意味 |
|---|---|
| `preserved` | コード系列一致率が R4-3 で定める閾値以上 |
| `knob_dead` | `chord_progression` を変えても performer 出力の `chord_events` が変わらない、または performer が欄を読まない |
| `sensor_blind` | source が現行センサーの有効帯域外（7th/sus/dim/aug 等）、または `compute_chord_events` が安定した列を返せない |
| `calibration_disagreement` | performer は欄を読み、センサーも帯域内だが、系列一致率が閾値未満 |

`sensor` には `compute_chord_events`、`sensor_state` には帯域内なら `working`、帯域外なら
`blind` を入れる。`grip` / `grip_class` は R4-2 の performer grip 測定で埋める。

数値物理ノブの近さとは異なり、コード進行は系列値である。`roundtrip_preservation.md` の
bpm trust のように許容差を直接 `abs(delta)` で書くのではなく、系列一致率を目盛りにする。

## fixity 方針

将来 `chord_progression` を正規事象欄に追加するなら、locked 可能な fixity 対象に含める
方針とする。コード進行は「作品同一性」を担う欄であり、R4 の目的はそれが往復で保存されるかを
見ることだからである。

ただし現状の `CompositionScore.fixity` は `PhysicalLayer` 専用なので、R4-2/R4-3 では
次の計画を明示してから実装する。

1. `CompositionScore` に事象層（例: `events`）と `chord_progression` を追加する。
2. `fixity` の allowed keys を `PhysicalLayer.model_fields` だけでなく、
   `events.chord_progression` のような事象欄キーまで拡張する。
3. `validate_fixity_matches_physical_values()` 相当の値照合を、物理層だけでなく事象層にも
   適用する。TODO sentinel / unlocked の表現を系列値にどう持たせるかは R4-2 の設計判断にする。

本 doc ではバリデータを変更しない。

## R4 分解の補正

`docs/score_centric_planning.md` §6 / §7 と `docs/roadmap_goal2.md` の古い R4 記述は、
事象センサーが学習モデルを要する可能性を前提にしている。しかしコード進行については、
決定論センサー `compute_chord_events` が既に存在する。よって R4-2 の
「学習モデル隔離実装（optional extra）」は、コード経路ではクリティカルパスではなく
精度 upgrade に降格する。

R4 の本丸は次の三つに補正する。

1. score 欄追加: `CompositionScore` に `chord_progression` を持つ事象層を追加する。
2. performer grip: `performer.py` が key 由来の固定進行ではなく、score のコード進行欄を読む。
3. 比較指標: `RoundtripField` にコード列を入れ、系列一致率から 4 値診断へ落とす。

この補正は stale 記述を消すものではなく、R4-1 の調査結果として surface するものとする。
旋律モチーフや複雑和声に進む場合は、改めて learned sensor の採用可否を検討する。

## 学習センサーの将来オプション

後で chord recognition や melody transcription の学習モデルを足す場合でも、
`docs/learned_models_policy.md` の隔離規約を維持する。学習出力は
`LearnedAudioAnnotations` に入れ、`PhysicalRPE.chord_events` や
`compute_chord_events` の決定論 path を汚染しない。学習モデルは「より広い有効帯域の
別センサー」として別名・別 provenance で併記し、R4 の主経路である deterministic
`chord_events` を置き換えない。

## Scope

R4-1 は docs 専用である。`src/**`、`config/**`、`tests/**`、`pyproject.toml` は変更しない。
`CompositionScore` への欄追加、performer の grip 配線、roundtrip comparator の実装、
fixity バリデータ拡張は R4-2/R4-3 の対象である。
