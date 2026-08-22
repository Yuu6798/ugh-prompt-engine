# AF-T0 Trait Transport Fidelity — 実行記録

- experiment_id: `AF-T0` / founder_id: `AF0`
- revision: **2**（実験 ID は増やさず revision を上げる運用）
- overall verdict: **NOT_ESTABLISHED**
- reason codes: ['AFTERGLOW_NOT_TRANSPORTED', 'COMBINATION_NOT_ESTABLISHED', 'ENERGY_NOT_TRANSPORTED', 'FRESH_CONFIRMATION_FAILED']

## AF-P0 との関係（§23）

- AF-P0 historical verdict remains NOT_ESTABLISHED.
- AF-T0 does not retroactively alter AF-P0.

## Localization（§6）

| trait | first_divergence_stage | worst total delta |
|---|---|---|
| duration | `WORLD_SYNTHESIS` | 22.314 |
| energy | `WORLD_SYNTHESIS` | 3.003 |
| afterglow | `PCM_PUBLICATION` | 59.361 |

## Transport（§7 / §22）

| trait | operator | mode | candidates tried |
|---|---|---|---|
| duration | `D1` | `sidecar` | ['D0', 'D1'] |
| energy | `None` | `None` | ['E0', 'E1', 'E2'] |
| afterglow | `None` | `None` | ['A0', 'A1'] |

> WORLD 単独保存ではなく、明示 Trait Sidecar を併用する transport architecture で保存された。

## Combined package（§19）

- **duration**: PASS (worst={'duration_onset': 3.8547091599394605, 'duration_share': 0.021754174254186576})
- **energy**: FAIL (worst={'energy_sustain': 2.9941677066037897})
- **afterglow**: PASS (worst={'afterglow': 0.8797423963907818})
- **sentinels**: PASS (failed=[])

## Gates（§21）

| gate | name | verdict |
|---|---|---|
| `T0-G0` | INPUT_FREEZE | PASS |
| `T0-G1` | BASELINE_REPLAY | PASS |
| `T0-G2` | STAGE_LEDGER_COMPLETE | PASS |
| `T0-G3` | DURATION_TRANSPORT | PASS |
| `T0-G4` | ENERGY_TRANSPORT | FAIL |
| `T0-G5` | AG_TRANSPORT | FAIL |
| `T0-G6` | SENTINEL_NON_REGRESSION | PASS |
| `T0-G7` | COMBINED_TRANSPORT | FAIL |
| `T0-G8` | DETERMINISM | PASS |
| `T0-G9` | PROVENANCE | PASS |
| `T0-G10` | FRESH_CONFIRMATION | FAIL |

## 主張上限（§2）

**言ってよい:**

- （PASS 時のみ）

**禁止:**

- AF-P0 は実は PASS だった
- WORLD は全形質を完全保存する
- 任意の人間音声で成立
- inheritance 成立
- mutation 成立
- crossover 成立

## 次段（§24 / §38）

- AF-T0 PASS して初めて AF-P1 Controlled Mutation へ進む。
- P0 と同じく、本 run は P1 へ自動進行しない（§34-15 STOP）。
