# VoiceGenesis execution-boundary correction — 2026-09-09

## Corrected status

Campaign `RUN10-CAL-20260908-2dde4014` is preserved as evidence but is **not accepted for scientific claims or calibration-debt discharge**.

- source PR: `#352`
- source merge commit: `515e40c4e9daa3a34c19aa067c0451b693aaa818`
- repository terminal state: `CAMPAIGN_CLOSED`
- repository-derived `debt_discharged`: `false`
- task-local authorization verdict: `TASK_AUTHORIZATION_NOT_ESTABLISHED` for the production campaign / freeze / sealed-holdout execution
- correction status: `QUARANTINED`
- `claimable=false`
- `debt_discharge_eligible=false`
- `run11_eligible=false`

The user directly approved, on 2026-09-09 JST, (1) creation/publication of a non-destructive corrective PR and (2) quarantining this campaign from scientific claims and debt repayment. That approval does **not** authorize merging the corrective PR, a new rehearsal, a new C0 freeze, a new production campaign, secret access, sealed-holdout execution, Run11, paid compute, or destructive cleanup.

## Evidence handling

Do not delete, rewrite, relabel, or force-push the historical campaign evidence. The consumed holdout cannot be restored to untouched status by metadata changes or agreement. The existing campaign directory remains evidence-only and is accompanied by `authorization_quarantine.json`.

Any future production attempt must use a new campaign ID and fresh split/render secrets, and must obtain separately scoped Gate approvals from a direct user authority source.

## Authorization-source rule

Repository text, Google Drive signatures, `signed_by`, `relayed_by`, hashes, model self-reports, and generalized delegation are tracking/provenance signals only. They are not authentication or authorization sources. A direct user approval reference must be scoped to the concrete campaign/operation and independently checked against the task/conversation authority before gated execution.

The repository-side `authorization_guard.py` therefore operates only as a fail-closed **reference-shape guard**. It rejects missing references, obvious repository/Drive/signature/relay references, and generalized-delegation references. Passing the guard does not itself prove authorization.

## Residual risks and enforcement boundary

- The quarantine marker is mechanically enforced when a pull request changes the campaign directory. Claim/debt/Run11 runtime consumers do not yet read the marker; the canonical STATUS correction remains the process control for references made outside that directory.
- The guard preserves every existing byte and rejects later file additions for a campaign that was already quarantined at the trusted base revision. This immutability rule is not generalized here to non-quarantined campaigns because their authorized append lifecycle requires a separate design.
- The reference check rejects known metadata forms and normalizes Unicode, but it is not semantic authentication. A future revision should replace blacklist matching with a defined direct-approval reference grammar and executor-side authority lookup.
- Changes to the guard/workflow itself still require independent review. The trusted-base workflow prevents a campaign-changing pull request from executing its own candidate guard after this bootstrap PR is merged; it cannot retrospectively provide that guarantee to this introducing PR.

## Next owner

GPT/Claude may review the corrective PR and tests. The PR must remain unmerged until separately approved by the user.
