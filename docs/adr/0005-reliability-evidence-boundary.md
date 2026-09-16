# ADR 0005: Reliability evidence is correlated, policy-bound, and non-authoritative

- Status: Accepted for v0.6
- Date: 2026-09-15
- Owners: VeriRun maintainers
- Related milestone: v0.6 Reliability & Evaluation Evidence

## Context

v0.1--v0.5 record immutable manifests, results, frozen plans, durable attempts,
artifacts, and bounded execution. Operators still need to connect an observed failure
to those records and determine whether reliability defects invalidate an evaluation
conclusion. The existing local kind evidence is a CPU trusted-fixture reference, not
a provider, GPU, multi-node, or general capacity environment.

## Decision

- OpenTelemetry API/SDK provides manual trace and metric instrumentation. A trace
  carries run, task, attempt, plan-digest, cohort, and artifact identifiers as
  attributes; it never authorizes a lease, state transition, or final-result commit.
- Structured evidence events carry the trace/span IDs required to correlate logs with
  durable metadata. Logs are not treated as a source of truth and are not required to
  use OpenTelemetry's experimental Python log signal.
- A versioned reliability policy declares evidence-integrity requirements,
  infrastructure-exclusion threshold, and minimum paired sample size. Missing
  lineage or plan/artifact mismatch invalidates a run. Excess allowed infrastructure
  exclusions make it partial. A small valid sample is reported as statistically
  insufficient, not silently converted into a capability conclusion.
- Statistical reports keep raw denominators, pass@k method, confidence interval,
  paired delta, fixed bootstrap seed, policy digest, and cohort/plan identity.
- Capacity and chaos reports record workload, environment identity, hypothesis,
  sample size, findings, and limitations. v0.6 only makes single-node CPU reference
  observations; it makes no generalized capacity or external-provider claim.

## Consequences

- Telemetry can be exported later without changing the evaluation evidence schema.
- Tests can exercise trace propagation, validity decisions, and statistics without a
  collector, cloud account, GPU, or provider credential.
- Any future multi-node or model-comparison conclusion requires a new recorded
  environment/cohort and cannot reuse v0.6's narrow reference claim.
