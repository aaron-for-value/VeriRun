# Architecture decision records

VeriRun records decisions that change public protocol identity, execution semantics, trust boundaries, or operational guarantees.

| ADR | Status | Decision |
|---|---|---|
| [0001](0001-canonical-protocol-and-hashing.md) | Accepted for v0.1 | Canonical protocol models, algorithm-qualified digests, and semantic replay hashing |
| [0002](0002-bounded-async-model-gateway.md) | Accepted for v0.2 | Bounded async generation, classified retries, and cancellation cleanup |
| [0003](0003-frozen-verification-plans-and-durable-commit.md) | Accepted for v0.4 | Frozen comparison plans, durable leases, and effectively-once authoritative result commit |
| [0004](0004-ray-data-and-core-execution-boundary.md) | Accepted for v0.5 | Ray Data ingestion, bounded Ray Core execution, and control-plane authority boundary |
| [0005](0005-reliability-evidence-boundary.md) | Accepted for v0.6 | Correlated, policy-bound observability and statistically valid reliability evidence |

## Lifecycle

- **Proposed** — open for review; not yet a supported contract.
- **Accepted** — governs the named milestone or release.
- **Superseded** — retained for history and linked to its replacement.
- **Rejected** — considered but not adopted; rationale remains visible.

Accepted ADRs are immutable except for typo or link corrections. A changed decision receives a new ADR that supersedes the old one.
