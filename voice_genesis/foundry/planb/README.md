# planb/ — Plan B PoC: Identity × Performance Skill 分離ハーネス

User 提供プラン「Run 8 が空振りだった場合の Identity × Performance Skill 分離案」を
実行可能な実測ハーネスにしたもの。**run 8 から独立した単独トラック**であり、
run 8 の設計・実測・判定に影響しない。

- 実験契約: [`DESIGN_PLANB_poc.md`](DESIGN_PLANB_poc.md)
- 第 1 走行記録: [`results_pb0/pb0_record_2026-08-20.md`](results_pb0/pb0_record_2026-08-20.md)

## 一行

Identity（spectral envelope / aperiodicity）と Performance（F0 / duration /
energy / release の **1 次元制御のみ**）を別経路で供給して WORLD で合成し、
R0–R4 ラダーで「どの成分が効いたか」を軸別に測る。Performance 側から
テクスチャが入る経路は**型と tripwire で塞いである**。

## 実行

```bash
pip install pyworld            # リポジトリの dev 依存には含めていない
cd voice_genesis/foundry/planb

python pb_ladder.py --out results_pb0/pos_control              # 陽性対照（合成故障あり）
python pb_ladder.py --out results_pb0/neg_control --no-fault   # 陰性対照（故障なし）
python pb_sweep.py                                             # TRF 軸の用量反応較正

python -m pytest tests -q -m "not slow"   # 単体（~2s）
python -m pytest tests -q                 # E2E 込み（~5min）
```

各走行は `record.json`（軸別計測 + ゲート判定 + 全 wav の sha256）と
`trf_gate_frozen.json`（R0 のみから凍結した事前登録ゲート）を出す。

## モジュール

| ファイル | 役割 |
|---|---|
| `pb_world.py` | WORLD 解析 / 合成の薄いラッパ + 対数スペクトル距離 |
| `pb_tracks.py` | `IdentityBank` / `PerformanceTrack` / `Unit` / トグル / R0–R4 ラダー定義 + 構造ゲート |
| `pb_extract.py` | `(wav, Unit 列)` → Identity / Performance。**代替と実資産で共通** |
| `pb_compose.py` | コア保存タイムワープ + F0 逸脱移植 + energy 置換 + release skill |
| `pb_metrics.py` | TRF 6 軸 / identity 距離 / performance 4 軸（**総合スコアは作らない**） |
| `pb_gates.py` | 受け入れゲート G1–G8 + 事前登録の凍結・判定 + tripwire |
| `pb_substitute.py` | 実資産不在時の代替ドナー（`singer/` R0.9）+ 合成故障注入 |
| `pb_ladder.py` | R0–R4 + 補助段 S1–S4 + P0 の実行器 |
| `pb_sweep.py` | 合成故障の深さ掃引による TRF 軸の較正 |

## 現時点の到達（第 1 走行）

| プラン受入条件 | 判定 |
|---|---|
| 4. 改善箇所の再現可能な帰属 / 5. 決定論 / 6. donor テクスチャ非使用 | **達成** |
| 3. held-out で重大回帰なし | **部分達成**（probe 2/5 のみ実在 = 3/5 が素材に不在） |
| 1. identity 聴感の維持 / 2. 実 TRF の改善 | **未達（実資産 + 耳が律速）** |

**リツ voicebank と PJS コーパスは本リポジトリ / 実行環境に存在しない。**
第 1 走行は `singer/` の決定論歌唱器 2 声を代替ドナーにした機構検証であり、
プラン §5 Case D（正式構造への昇格）は宣言していない。

## 実資産で走らせるとき

コードは分岐しない。必要なのは wav と Unit 境界の 2 つだけ:

```python
units = pb_tracks.units_from_boundaries(labels, boundaries_s, terminal_flags=...)
bank  = pb_extract.build_identity_bank(pb_world.analyze(ritsu_wav, sr), units, source_id="ritsu")
perf  = pb_extract.build_performance_track(pb_world.analyze(pjs_wav, sr), pjs_units, source_id="pjs")
```

`--no-fault` で走らせること（合成故障は代替素材専用の陽性対照）。
