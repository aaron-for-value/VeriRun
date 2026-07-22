# Versioning and release evidence

VeriRun uses semantic versioning for the Python package and evidence-gated GitHub milestones for capability delivery.

## Pre-1.0 policy

- Minor versions (`0.x`) may introduce intentional protocol changes.
- Patch versions (`0.x.y`) preserve documented protocol compatibility unless a security fix makes that impossible.
- Development versions use `.devN` and do not represent supported releases.
- Every incompatible schema change increments its embedded schema version even when the package remains pre-1.0.

## Release gate

A release requires:

- milestone exit evidence reproduced from the tagged revision;
- a clean compatibility matrix;
- passing required GitHub Actions checks;
- installation and reproduction commands;
- security assumptions and known limitations;
- migration notes for changed schemas or commands;
- no unresolved critical security or result-integrity defect.

## Evidence identity

Release notes link to evidence but do not replace it. Evidence records the source revision, environment, workload identity, inputs, verifier, artifacts, and commands needed to reproduce the claim.
