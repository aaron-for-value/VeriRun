# ADR 0004: Ray Data ingestion and Ray Core execution stay below the durable control plane

- Status: Accepted for v0.5
- Date: 2026-09-14
- Owners: VeriRun maintainers
- Related milestone: v0.5 Distributed Executor

## Context

v0.4 makes PostgreSQL authoritative for frozen verification plans, run/task/attempt
state, leases, command idempotency, and final-result uniqueness. v0.5 must distribute
execution without allowing framework retries to change that contract. It also needs
versioned manifest ingestion and bounded execution across CPU verifier, GPU inference,
and external API capacity classes.

## Decision

- Ray Data normalizes immutable manifest-derived work records and creates bounded,
  deterministic shards. It does not claim leases, choose a verifier, or commit a
  result.
- Ray Core executes already-claimed attempts. Each submitted work item carries the
  plan ID/digest, run/task/candidate identity, attempt ID, and opaque lease token.
- The driver is the only component that calls the v0.4 control plane for claim,
  heartbeat/reclaim, and final-result commit. A Ray task returns an immutable result
  envelope; the driver verifies plan lineage before attempting the authoritative
  commit.
- `ray.wait` (or an equivalent bounded completion set) limits submitted-but-unresolved
  work. The driver never creates an unbounded object-reference collection.
- Ray task retries and actor replacement are execution retries only. They must either
  keep the original live lease or let it expire and be reclaimed as a new attempt.
  They are not business idempotency and cannot write a final result directly.
- CPU verifier, GPU inference, and external API capacity use explicit named Ray
  resources. M4's reference environment supplies CPU trusted fixtures only; GPU/API
  resource names validate admission semantics but do not establish GPU performance or
  provider-reliability claims.
- PostgreSQL, S3-compatible artifacts, plan compilation, cohort aggregation, and
  final-result commit deliberately remain outside Ray. PostgreSQL preserves the
  transaction/uniqueness guarantee; S3 preserves immutable artifact-byte identity.

## Consequences

- A worker/actor crash may repeat execution but cannot replace a frozen plan or create
  a second authoritative result.
- Ray Data and Ray Core are independently testable: ingestion/sharding is a pure
  data-contract test, while execution is tested against control-plane leases and
  commit conflicts.
- KubeRay `RayJob` cleanup terminates Ray infrastructure; cancellation and final
  state are still recorded through the control plane.
- M4 adds no Ray source change. A framework defect requires an isolated reproduction
  before any upstream patch is considered.
