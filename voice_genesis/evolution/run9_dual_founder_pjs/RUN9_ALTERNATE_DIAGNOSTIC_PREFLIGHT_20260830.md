signed_by: GPT

# RUN9 alternate diagnostic candidate preflight — 2026-08-30

## 0. Decision boundary

This is a machine-dependent preflight record for the quarantined alternate
diagnostic defined by `RUN9_ALTERNATE_ATTEMPT_PLAN_20260830.md`.  It is not a
formal RUN9 attempt, is not a Birth Gate result, and does not amend the
append-only conclusion in `BIRTH_GATE_ATTEMPT_20260828.md`.

The preregistered acoustic candidate remained fixed at
`80a40f9ebee3f486de8e48c3911b188a6a4652147dd9e02dfcd90ef2f9eac646`.
The preflight reproduced all required source assets but generated a different
acoustic ONNX digest.  Execution therefore stopped before renderer
construction, before the 84-render diagnostic, and before evidence
publication.

| Field | Recorded value |
|---|---|
| repository head | `b3dfd19d3bd5d014bf90e4cff75f0f6fb17cb9fd` (PR #338 merge commit) |
| preflight disposition | `CANDIDATE_BYTE_IDENTITY_MISMATCH` |
| expected candidate SHA256 | `80a40f9ebee3f486de8e48c3911b188a6a4652147dd9e02dfcd90ef2f9eac646` |
| observed acoustic SHA256 | `463d04839b342ea666cac1f5dcd8248d1cbe825494a1ddf646581b9a0ac6ca53` |
| observed acoustic bytes | `279777001` |
| candidate-bound diagnostic issued | no |
| diagnostic renders admitted | `0 / 84` |
| diagnostic bundle published | no |
| formal Birth Gate evidence | no |
| learning progression | prohibited |

## 1. Source asset verification

The following source assets were fetched into an isolated, repository-external
work directory and verified before use.  No source binary was committed to the
repository.

| Asset | SHA256 | Result |
|---|---|---|
| RUN6 phase-B 40K checkpoint | `6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a` | match |
| checkpoint `config.yaml` | `3722072045060e316ec9fee3f307412eceacf617d3b3ece7adfcbefa0f9df9d9` | match |
| checkpoint `spk_map.json` | `da9748fabfa721a4a789224b50fd52743628fd2396602852f2dc25c54f2e3803` | match |
| checkpoint `lang_map.json` | `2a6a227ee65a49f5c30e848a4b62c5cc1817926bbdab373228e6302d2c794953` | match |
| checkpoint `dictionary-ja.txt` | `b8ea0d99fcf60e82319cc84b162d9e1b4d5ce1146cfa1c6291e025fbb8be14ef` | match |
| `NamineRitsu_DiffSinger.zip` | `5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530` | match |
| `nsf_hifigan.oudep` | `e22f84009804da2e5916e7a2000f4c30278148796376e49368ec5ff8f9f58830` | match |
| `PJS_corpus_ver1.1.zip` | `683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca` | match |
| expanded PJS corpus identity | `9905cec08fbaf43fa545400498a7908ef28567e8f60a5ba005fb2e00d526f996` | match |

The four canon runtime members and the extracted vocoder also matched their
existing ledger values:

- `linguistic.onnx`: `1c9ec9f67277a2ba4b9c3f815150251ed7d87ad54eed3e22f8d85dbda74705b6`
- `dsdur/dur.onnx`: `11bbfad5c489a57e05bd6ed7e239b3fce913a6b644d9281ae152126563a3d288`
- `dspitch/pitch.onnx`: `e361ad13053c4b49331a44296148bb33396092f57ca477ceed60e59cdbdfb3b9`
- `phonemes.txt`: `1489af3c4806ad2cfc10e663ec27a1bf7c6bf0d6f9a047263948c5cbe36eebfb`
- `nsf_hifigan.onnx`: `a3e26672a8c655e3faf65f31cb4339a7fbca7758ba86be9af89e03dced7c3fa4`

## 2. Export environment and command

- CPython: `3.11.15`
- exporter: clean detached `openvpi/DiffSinger` checkout at
  `e2307b1080b00f3999702ce9017cfd75c7f862fe`
- package closure: all 81 entries in
  `inputs/reexport_manifest.json#export_environment_lock` matched
  `pip freeze --all` exactly
- operating system: Ubuntu `24.04.3 LTS`, kernel `6.18.35`, `x86_64`
- CPU: Intel Xeon Platinum 8573C, 9 logical CPUs available

The export command was the frozen replay command with an explicit interpreter
and a previously nonexistent output directory:

```text
<isolated-venv>/bin/python scripts/export.py acoustic \
  --exp s5_run6_acoustic_v1 \
  --ckpt 40000 \
  --out <isolated-workdir>/onnx_gate_40000
```

## 3. Closed-world export result

The output directory contained exactly the nine expected filenames.  Eight
non-acoustic artifacts matched the current reexport manifest byte-for-byte:

| Artifact | Observed SHA256 | Result |
|---|---|---|
| `dsconfig.yaml` | `a7da75f5c403bd347f108ded6ea6925df6260dae83cf72877c5b19018443899c` | match |
| `s5_run6_acoustic_v1.phonemes.json` | `5071e1654c4572d90011a49959b97467b6bed5ecf08c203b71b9aff4b02807a8` | match |
| `s5_run6_acoustic_v1.languages.json` | `a51ee3aa7dafa1905b01a8c6ed2e99ebeecad0071d786493f43effd2438b2fda` | match |
| `dictionary-ja.txt` | `b8ea0d99fcf60e82319cc84b162d9e1b4d5ce1146cfa1c6291e025fbb8be14ef` | match |
| `s5_run6_acoustic_v1.ritsu.emb` | `ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c` | match |
| `s5_run6_acoustic_v1.pjs.emb` | `074e09b390c207a7cf98105db549e1006d035a797d57f73e103e848bb3216015` | match |
| `s5_run6_acoustic_v1.user.emb` | `588913b74d6c16e01f4f33223698cd165ac686012e7d878475a3799ccee8bde0` | match |
| `s5_run6_acoustic_v1.d3synth.emb` | `10c3964c57a69edb072bd7c9aec36dc7e3b06e06469c5da60332bec793c1dc22` | match |

The acoustic file had the expected filename and byte size but SHA256
`463d04839b342ea666cac1f5dcd8248d1cbe825494a1ddf646581b9a0ac6ca53`.
It was neither substituted for the preregistered `80a40f...` candidate nor
admitted to `GateSynthRenderer`.

## 4. Fail-closed conclusion

This preflight establishes only that the available pinned source assets and
the frozen export package closure produced a third acoustic byte identity on
the recorded machine.  It does not establish a cause for the difference and
does not establish repeatability of `463d048...`; the first mismatch was a hard
stop, so no second export was run.

`RUN9_CONTRACT.yaml`, `inputs/reexport_manifest.json`,
`dependency_pins_sha`, and `learning_recipe_sha` remain unchanged.  No Birth
scientific outcome (`ESTABLISHED` or `NOT_ESTABLISHED`) is asserted.  Continuing
requires either the exact preregistered `80a40f...` bytes/a reproducing
environment, or a new User-approved design decision that selects a different
candidate and defines a new attempt boundary.

-- GPT
