# ADR 0003: Frozen verification plans and durable result commit

- Status: Accepted for v0.4
- Date: 2026-09-02
- Owners: VeriRun maintainers
- Related milestone: v0.4 Durable Control Plane

## Context

Evaluation candidates are only comparable when they are judged by the same verifier
graph, evidence policy, image/config versions, aggregation policy, and budget rules.
Selecting or changing those inputs after observing a candidate makes a score cohort
ambiguous. At the same time, worker and control-plane failures require retryable
attempts, so execution cannot be assumed to happen once.

## Decision

v0.4 introduces a deterministic `VerificationPlan` compiler and registry.

- The compiler receives task/evaluation intent, verifier catalog, policy revision,
  input-evidence digests, aggregation policy, cohort request, and budget inputs.
- Candidate content, model identity, and evaluation output are not selection inputs.
- The canonical plan digest freezes the verifier graph and all comparison-affecting
  policies before scheduling.
- Plans follow `draft → validated → frozen → superseded|invalid`; only `frozen` is
  schedulable.
- A changed plan digest splits the effective comparison cohort, and aggregation rejects
  mixed plan/cohort inputs.
- PostgreSQL stores immutable command receipts, run/task/attempt state, lease ownership,
  and final results transactionally.
- Execution is at least once. A composite final-result key provides effectively-once
  authoritative results, while equivalent repeated commits return the existing row.
- Content-addressed artifact bytes live in an S3-compatible store and are linked by
  SHA-256 metadata in PostgreSQL.

## Alternatives considered

### Select verifiers per candidate

Rejected for v0.4 because candidate-adaptive or LLM-routed selection changes the
measurement contract after comparison subjects are known. It may be studied later in
shadow mode with separate cohorts and validity evidence.

### Treat worker execution as exactly once

Rejected because crashes and ambiguous network outcomes make exactly-once execution
unrealistic. The useful guarantee is repeatable attempts plus one authoritative final
record.

### Add Kafka or Temporal first

Rejected because the milestone needs explicit state and transaction semantics before
another coordination system. PostgreSQL row locks, leases, and uniqueness constraints
are sufficient for the M3 evidence scale.

### Store artifact bytes in PostgreSQL

Rejected because large immutable bytes and transactional metadata have different
storage and lifecycle needs. The database retains content identity and location; S3
retains bytes and supports independent integrity checks.

## Consequences

- Workers must carry plan ID/digest lineage and cannot silently recompile a plan during
  retry or takeover.
- Operators must run PostgreSQL and an S3-compatible store as a coordinated recovery
  set.
- Superseding or invalidating a plan prevents new claims; already-authoritative results
  remain auditable.
- Idempotency keys are part of the public command contract. Reusing a key for changed
  intent is an error, not an update.
- v0.4 does not include distributed Ray execution, dynamic plan routing, a UI, or a
  production high-availability claim.
