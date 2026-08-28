# RUN9 Birth Gate rev 0.6 execution attempt — 2026-08-28

## 0. Terminal decision

This is an append-only execution record for the first Birth Gate attempt after
PR #335 was merged.

| Field | Recorded value |
|---|---|
| repository head at attempt start | `28e69d579417ccfd2a99ff7c9476b28ce37b5bfb` (PR #335 merge commit) |
| `run_status` | `IMPLEMENTATION_FAILED` |
| `failure_class` | `IMPLEMENTATION_FAILURE` |
| Birth Gate overall PASS | `false` |
| completion detail | `IDENTITY_PROTOCOL_AUDIT_INCOMPLETE` |
| identity-establishment measurement | not evaluated; no BIRTH scientific outcome is claimed |
| learning progression | prohibited |
| `learning_recipe_sha` | remains `PENDING`; no recipe build or PIN was performed |
| promotion | prohibited |

This classification follows
`identity_decision_protocol_v0.6.json#birth_gate_overall_pass.completion_evidence_requirement`:
missing or partial C0/C1/positive-reference evidence is an
`IDENTITY_PROTOCOL_AUDIT_INCOMPLETE` outcome in the existing
`IMPLEMENTATION_FAILURE` class. It is not evidence that the two Founder
features collapsed, so this record does not invent a scientific
`BIRTH=NOT_ESTABLISHED` result.

## 1. Preconditions verified

- PR #335 was present at `origin/main`; the attempt used a clean branch from
  merge commit `28e69d579417ccfd2a99ff7c9476b28ce37b5bfb`.
- The five PRACTICE refreeze pins introduced by PR #335 were present and
  non-`PENDING`:
  `practice_alignment_spec_sha`, `practice_actor_input_manifest_sha`,
  `practice_audit_annotation_manifest_sha`, `loss_evaluator_spec_sha`, and
  `learning_data_binding_manifest_sha`.
- RUN6 phase-B 40K checkpoint bytes matched
  `6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a`
  (`556013282` bytes).
- Checkpoint-side `config.yaml`, `spk_map.json`, `lang_map.json`, and
  `dictionary-ja.txt` all matched the hashes in `reexport_manifest.json`.
- DiffSinger was a clean detached checkout at
  `e2307b1080b00f3999702ce9017cfd75c7f862fe`.
- The replay venv used CPython `3.11.15`. `pip freeze --all` matched all 81
  entries in `reexport_manifest.json#export_environment_lock` exactly, with no
  missing or additional package.
- The canon linguistic/duration/pitch/phoneme assets and the pinned
  `nsf_hifigan.onnx` were independently retrieved and their raw SHA256 values
  matched the dependency ledger. They were not consumed after the acoustic
  replay precondition failed.

## 2. Fail-closed replay result

The manifest replay command was executed from the clean DiffSinger checkout
with the explicitly addressed venv interpreter and a previously nonexistent
output directory.

The first run in the managed workspace produced the expected nine named files,
but workspace large-file synchronization also created an incomplete hidden
copy. To remove that storage mechanism from the closed-world comparison, the
command was repeated in a fresh `/tmp` directory outside the synchronized
workspace. The `/tmp` run contained exactly the nine manifest filenames.

Eight artifacts matched both expected byte size and expected SHA256. The
acoustic ONNX had the expected size but not the pinned digest:

| Artifact | Expected | Observed run 1 | Observed run 2 | Bytes |
|---|---|---|---|---:|
| `s5_run6_acoustic_v1.onnx` | `cdbd779c686504cd1277bf74036e5fb334e4fcdc88ab7612f435ced7c1d6687b` | `80a40f9ebee3f486de8e48c3911b188a6a4652147dd9e02dfcd90ef2f9eac646` | `80a40f9ebee3f486de8e48c3911b188a6a4652147dd9e02dfcd90ef2f9eac646` | 279777001 |

The two independent observed outputs were byte-identical. A third diagnostic
export restricted to four logical CPUs also produced the same `80a40f...`
digest. Therefore the current environment is internally deterministic, but it
does not reproduce the already-pinned `cdbd779c...` bytes.

The recorded execution profile and current runtime differ below. The evidence
does not identify which native component causes the byte delta, so no causal
claim is made.

| Property | Pinned/recorded replay environment | Attempt environment |
|---|---|---|
| CPU | Intel Xeon Processor @ 2.10GHz | AMD EPYC 9V74 80-Core Processor |
| logical CPUs | 4 | 9 (plus a 4-CPU affinity diagnostic) |
| Ubuntu image | 24.04.4 LTS | 24.04.3 LTS |
| kernel | Linux 6.18.44-fc-v21 | Linux 6.18.35 |
| architecture | x86_64 | x86_64 |

Under `reexport_manifest.json#replay_environment_recipe`, any output byte
mismatch makes replay fail. The observed ONNX was not substituted, and neither
the manifest nor contract pin was changed.

## 3. Birth Gate evidence accounting

Because acoustic replay failed before a valid pinned render runtime could be
assembled, no Founder birth-probe render was admitted:

| Required rev 0.6 evidence | Completed |
|---|---:|
| C0 R9F-01 exact-replay takes | 0 / 20 |
| C0 R9F-02 exact-replay takes | 0 / 20 |
| C1 R9F-01 exact-replay takes | 0 / 20 |
| C1 R9F-02 exact-replay takes | 0 / 20 |
| positive-reference audit, R9F-01 | no |
| positive-reference audit, R9F-02 | no |
| valid/finite Founder features and finite `d12 > 0` | not measured |
| positive finite PJS-confuser distances for both Founders | not measured |
| `birth_gate_overall_pass` completion evidence | incomplete |

This is a non-PASS attempt and learning must not proceed. No epsilon, Founder
coordinate, speaker-map weight, metric feature, or ONNX pin was changed to
rescue the attempt.

## 4. Additional implementation blocker confirmed

The repository still has no VG-L0 Birth Probe execution/consumption harness.
This was already declared before the attempt in
`inputs/failure_abort_criteria.json` rules 5–7: SingerState/birth-probe render
determinism, dual-birth viability, and consumption-time mapping of measured
Birth Gate results are PROCEDURAL pending harness implementation. The existing
`voice_genesis/foundry/s1_gate/gate_synth.py` is a render entrypoint, not the
rev 0.6 closed-world C0/C1/positive-reference/d12/PJS result executor.

## 5. Reopen conditions

The same design revision may be retried only after both implementation
conditions are met:

1. provide the pinned acoustic ONNX bytes with SHA256 `cdbd779c...`, or execute
   the existing replay recipe in an environment that reproduces those exact
   bytes; and
2. implement and review the VG-L0 Birth Probe executor/consumer that emits the
   complete rev 0.6 evidence set and validates it against
   `birth_gate_overall_pass`.

Repinning `cdbd779c...` to the current `80a40f...` output, changing the replay
environment contract, or changing Birth Gate measurement semantics requires a
separately approved design revision. None was performed in this attempt.
