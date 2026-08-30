signed_by: GPT

# RUN9 alternate attempt plan — 2026-08-30

## 0. Status and authority

This is a pre-execution plan for a separate RUN9 attempt. It records the
implementation interpretation of the User's verbatim adjudication in
`USER_ADJUDICATION_20260830_ONNX_ALTERNATE_ATTEMPT.txt`.

This document does **not** amend the append-only conclusions in
`BIRTH_GATE_ATTEMPT_20260828.md`, does **not** repin the rev 0.6 contract, and
does **not** assert a Birth Gate result. The existing rev 0.6 attempt remains
terminal `IMPLEMENTATION_FAILED` with zero admitted renders.

## 1. Candidate byte identity

The only acoustic ONNX admitted to the alternate diagnostic is the candidate
already measured independently in the terminal attempt record:

| Field | Frozen candidate |
|---|---|
| acoustic ONNX SHA256 | `80a40f9ebee3f486de8e48c3911b188a6a4652147dd9e02dfcd90ef2f9eac646` |
| byte size | `279777001` |
| source attempt record SHA256 | `528d22e18665a99b4be261bc0bfdb155fd21799bf73eae5faa8481c50d2b5874` |
| observed repeatability | two independent exports plus one four-logical-CPU diagnostic export produced the same bytes |

No other acoustic digest may be substituted. The candidate is not a fallback
for the pinned `cdbd779c...` bytes and is not a formal rev 0.6 input.

## 2. C1 synthesis attachment semantics

The `ZERO_CONTROLPROFILE_SHAM` attachment is fixed as an inert, closed-world
integration-boundary operation:

- the profile schema is `run9-control-profile/1.0`;
- `branch=CONTROL`, `revision=r_sham`, and `parent_revision=r0` are mandatory;
- `trait_control` and `technique_control` must both be empty objects;
- the RUN9 adapter validates and consumes the profile immediately before the
  pinned `gate_synth.run_pipeline` call, then passes the same attestation-bearing
  `record` object into that synthesis call;
- consumption produces an exact attestation containing status, Founder
  `voice_id`, revision, and `profile_id`;
- any missing, malformed, non-empty, or mismatched attachment aborts before
  evidence publication.

The pinned `gate_synth.py` bytes remain unchanged, preserving their historical
smoke provenance. This makes the RUN9 attachment operationally real while
guaranteeing that it applies no performance parameter change.

## 3. Non-adjudicative diagnostic boundary

The candidate may first be exercised only through the quarantined diagnostic
API in `birth_probe_executor.py`. It uses the fixed 84-render order and current
rev 0.6 measurement predicates solely to test executor feasibility. Its output
has all of the following irreversible boundaries:

- schema `run9-birth-probe-non-adjudicative-diagnostic/1.0`;
- disposition `QUARANTINED_NON_ADJUDICATIVE`;
- `formal_birth_gate_evidence=false`;
- `formal_birth_gate_overall_pass=null`;
- `learning_progression_allowed=false`;
- the nested measurement snapshot omits `overall_pass`,
  `learning_progression_allowed`, and its formal evidence seal;
- publication requires an output directory prefixed
  `non_adjudicative_`, writes `QUARANTINE.json`, and never writes
  `birth_gate_evidence.json` or the formal artifact-manifest schema;
- the formal publisher rejects the diagnostic schema.

The diagnostic does not permit `learning_recipe_sha` build/PIN even if all
current measurement predicates are observed true.

## 4. Formal refreeze after diagnostic

A formal alternate Birth Gate attempt requires a later, separately reviewed
refreeze. That change must at minimum:

1. create the new design revision and attempt identifier;
2. pin a complete reexport manifest whose acoustic bytes equal the candidate;
3. close `dependency_pins_sha` over the actually executed render and learning
   import closure;
4. update every affected contract/document provenance pin without changing the
   historical rev 0.6 attempt;
5. run the formal executor from the new pins and publish only its formal schema.

Only a complete formal bundle with C0/C1 20 takes per Founder, both positive
references, finite `d12 > 0`, finite positive PJS-confuser distances, no audit
stops, and `overall_pass=true` may unlock the next stage. Otherwise freeze at
`NOT_ESTABLISHED` or the applicable implementation/design failure and stop.

## 5. Current stop conditions

At this commit the C1 attachment and quarantine machinery are implemented, but
the candidate diagnostic has not run because the external model/corpus assets
are not present in this workspace. `dependency_pins_sha` and
`learning_recipe_sha` remain `PENDING`; no contract pin is changed here.

-- GPT
