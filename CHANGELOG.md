# Changelog

All notable VeriRun changes are documented here. The project follows [Semantic Versioning](docs/VERSIONING.md) once a release is tagged.

## Unreleased

### Added

- Deterministic, versioned `VerificationPlan` compilation and lifecycle with frozen
  verifier/evidence/aggregation/budget inputs and automatic comparison-cohort splitting.
- PostgreSQL-backed run, task, lease, command, final-result, and artifact metadata with
  restart recovery, heartbeat/reclaim, immutable idempotency receipts, and one
  authoritative result per run/task/candidate/plan digest.
- S3-compatible SHA-256 artifact storage, typed `verirun control` lifecycle commands,
  public control-plane JSON Schemas, and local PostgreSQL + MinIO recovery evidence.

### Changed

- Legacy v1 task-attempt/result records may now carry an optional paired verification
  plan ID and digest; the durable M3 path always records the plan lineage in its own
  task, lease, and final-result records.

### Security

- M3 makes no exactly-once execution, multi-tenant authorization, database failover,
  object-store outage tolerance, or production-readiness claim.

## [0.3.0] - 2026-08-30

### Added

- Digest-pinned development-container executor with an explicit, local-only threat
  boundary.
- Restricted Kubernetes Job executor with explicit context, namespace, RuntimeClass,
  default-deny-egress preflight, bounded logs, and mandatory cleanup.
- Local `kind` + gVisor (`RuntimeClass(handler: runsc)`) attack/replay evidence for
  the Kubernetes tier, including runtime identity and no-residual-workload checks.

### Security

- Published the supported local runtime boundary and known limitations. This release
  is not a production-sandbox, absolute-security, or cross-distribution Kubernetes
  compatibility claim.

## [0.2.0] - 2026-08-17

### Added

- Bounded OpenAI-compatible async generation gateway with independent concurrency, QPS,
  and in-flight-token admission controls.

## [0.1.0] - 2026-08-16

### Added

- Immutable v0.1 manifest, attempt, artifact, verification, and replay records.
- Exported JSON Schemas kept in sync with the public Pydantic records by CI.
- Canonical JSON and SHA-256 content identity.
- Content-addressed local artifact storage with integrity checks.
- A development-only local executor for trusted fixtures with structured failure classification.
- `verify`, `replay`, `smoke`, and optional `evalplus-smoke` CLI workflows.
- EvalPlus v0.3.1 compatibility adapter and labeled HumanEval+ subset evidence workflow.
- Python 3.12 CI, static analysis, coverage, build, and synthetic replay gates.
- A manually dispatched Linux EvalPlus compatibility workflow with uploaded evidence.
- Source revision and working-tree provenance in generated evidence reports.
- Checked-in synthetic and HumanEval+ compatibility replay evidence bound to the v0.1 implementation revision.

### Security

- The local executor is explicitly restricted to trusted fixtures and makes no hostile-code isolation claim.
