# VeriRun Roadmap

This roadmap turns VeriRun from a design into an evidence-backed executable evaluation and reward runtime. It is organized by capability gates, not speculative dates.

**Current stage:** v0.1.0 released; v0.2 planning is queued

**Last updated:** 2026-08-16

**Release policy:** a milestone closes only when its evidence is reproducible from the tagged revision.

## How to read this roadmap

Each milestone defines four things:

- **Outcome** — the user-visible or operator-visible capability.
- **Scope** — the implementation owned by this milestone.
- **Exit evidence** — artifacts and tests required before the milestone can close.
- **Not in scope** — explicit boundaries that prevent infrastructure creep.

Statuses used in this document:

- **Verification** — implementation exists; exit evidence and review are in progress.
- **Current** — the active implementation milestone.
- **Complete** — exit evidence is published and the milestone is closed.
- **Queued** — accepted direction, not yet started.
- **Optional** — pursued only after core gates and available capacity.
- **Future** — requires evidence from earlier releases before its scope is credible.

Dates will be added to GitHub milestones only after the repository exists remotely and the maintainer has made a realistic capacity decision. Missing dates are deliberate, not missing planning.

## North star

Given an immutable evaluation or reward manifest, VeriRun should be able to:

1. generate or ingest candidates under bounded concurrency;
2. run untrusted verification in the declared isolation environment;
3. preserve every attempt and commit exactly one final result;
4. resume or replay without silently changing model or verifier inputs;
5. trace results to benchmark, prompt, candidate, tests, image, policy, and artifacts;
6. distinguish capability failures from infrastructure failures;
7. expose the same verifier path to asynchronous training reward clients.

The project reaches v1.0 only when these properties are demonstrated across supported environments and documented with reproducible evidence.

## Milestone overview

| Milestone | Outcome | Status |
|---|---|---|
| Foundation | Public contract, governance baseline, and GitHub operating model | **Verification** |
| v0.1 — Protocol Baseline | Reproducible verification and frozen-candidate replay | **Complete (v0.1.0)** |
| v0.2 — Async Model Gateway | Bounded model generation with correct failure and cancellation semantics | Queued |
| v0.3 — Isolated Execution | Explicit local/container/Kubernetes execution tiers and attack evidence | Queued |
| v0.4 — Durable Control Plane | Recoverable runs, leases, heartbeats, replay, and idempotent result commit | Queued |
| v0.5 — Distributed Executor | Bounded Ray/KubeRay execution with failure recovery | Queued |
| v0.6 — Reliability & Evaluation Evidence | Correlated observability, capacity/chaos reports, and valid statistics | Queued |
| v0.7 — Reward Runtime | Stable asynchronous verifier rewards for veRL | Queued |
| v0.8 — Agent Workloads | Harbor/TB2 integration with three-stage failure attribution | Optional |
| v1.0 — Stable Runtime | Evidence-backed supported contracts and compatibility policy | Future |

## Foundation — Repository contract

### Outcome

A public repository that states what VeriRun is, what it is not, how work is accepted, and which claims are currently supported.

### Scope

- [x] Define the product as an executable evaluation and reward runtime.
- [x] Publish architecture, scope boundaries, maturity, and security caveats in `README.md`.
- [x] Publish evidence-gated releases in `ROADMAP.md`.
- [x] Select and add the Apache-2.0 license.
- [x] Add `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and `SECURITY.md`.
- [x] Add issue forms and a pull-request template.
- [x] Create the public GitHub repository and bind the local `origin`.
- [ ] Configure branch protection/rulesets for `main` after the first CI workflow exists.
- [x] Create initial labels and milestones. The optional project board awaits GitHub Projects token scope.
- [x] Document versioning, compatibility, and release evidence policy.

### Exit evidence

- GitHub community profile checks have no unexplained gaps required for this stage.
- The README contains no unsupported quickstart, benchmark, security, or scale claims.
- Every planned release maps to a GitHub milestone and has an issue-level acceptance path.
- The repository has an explicit license and private security reporting path before executable code is published.

### Not in scope

- Runnable evaluation code.
- Performance claims.
- Model or benchmark scores.

## v0.1 — Protocol Baseline

### Outcome

Run trusted EvalPlus fixtures through a stable protocol, preserve their lineage, and reproduce frozen verification results without a model call.

### Scope

- [x] Versioned `EvalManifest`, `Candidate`, `TaskAttempt`, and `VerificationResult` schemas.
- [x] Canonical serialization and content hashing.
- [x] EvalPlus adapter for versioned HumanEval+ and MBPP+ task inputs.
- [x] Oracle, obvious-failure, syntax-failure, and boundary-failure fixtures.
- [x] Executor interface with an explicitly unsafe development-only local backend.
- [x] Structured result classes: `passed`, `compile_error`, `test_failure`, `timeout`, `oom`, `policy_violation`, and `infra_error`.
- [x] Artifact layout and machine-readable replay comparison.
- [x] CLI for baseline verification and report generation.

### Exit evidence

- Schema, hash, split/protocol, timeout, and result-classification tests pass in CI.
- A frozen smoke subset replays twice with deterministic semantic results.
- The labeled HumanEval+ compatibility subset records package, dataset, subset, candidate, and verifier identity without presenting a standard score.
- MBPP+ adapter compatibility is versioned, but an MBPP+ execution claim requires separate published evidence.
- Raw and Plus test differences are reported without mislabeling subset results as full benchmark scores.
- Every result resolves to candidate, tests, runner identity, and manifest.
- `BENCHMARK_PROTOCOL.md`, a baseline report, and replay fixtures are published.

Release evidence:

- [Synthetic protocol smoke](evidence/v0.1/synthetic/REPORT.md)
- [EvalPlus HumanEval+ compatibility smoke](evidence/v0.1/evalplus/REPORT.md)
- implementation revision: `b30b11d2e3e1b20ad7c3b7e3df3f314ae7d6a64c`

v0.1.0 was released after required GitHub CI passed, PR [#7](https://github.com/aaron-for-value/VeriRun/pull/7) merged, and the [Linux EvalPlus compatibility workflow](https://github.com/aaron-for-value/VeriRun/actions/runs/31953267994) completed successfully. See the [v0.1.0 release](https://github.com/aaron-for-value/VeriRun/releases/tag/v0.1.0).

### Not in scope

- A real model endpoint.
- Distributed execution.
- A security claim for the local backend.
- PostgreSQL, Ray, Kubernetes, or an object store.

## v0.2 — Async Model Gateway

### Outcome

Generate candidates through an OpenAI-compatible endpoint with bounded resource use and predictable cancellation, timeout, and retry behavior.

### Scope

- [x] Async HTTP connection pooling.
- [x] Separate concurrency, QPS, and token-budget controls.
- [x] Bounded producer/consumer queues rather than unbounded task creation.
- [x] Layered timeouts and structured cancellation.
- [x] Error classification and retry budgets with jitter.
- [x] Fake server scenarios for 429, 5xx, slow response, disconnect, and malformed JSON.
- [x] Candidate generation replay boundary independent from verification replay.

### Exit evidence

- [x] Tests cover fail-fast behavior, partial failure, parent cancellation, timeout cleanup, and backpressure.
- [x] Cancellation leaves no hanging internal task or client connection; v0.2 creates no child process.
- [x] Sequential, eager-task, and bounded-worker submission are compared with throughput, traced memory, and admitted-request P95 evidence.
- [x] Retry amplification and root-cause preservation are reported.

### Not in scope

- Provider-specific orchestration beyond the OpenAI-compatible contract.
- Distributed rate limiting.
- Claims that async provides CPU parallelism.

## v0.3 — Isolated Execution

### Outcome

Run untrusted candidates through explicit execution tiers and publish security evidence for the strongest supported tier.

### Scope

- Threat model covering assets, attackers, trust boundaries, and residual risk.
- Development container backend.
- Kubernetes Job backend with restricted pod settings, resource limits, default-deny networking, artifact allowlists, and bounded logs.
- gVisor `RuntimeClass` validation on Linux/Kubernetes.
- Cleanup and duplicate-final-result handling across Job retries.
- Attack regression suite.

### Exit evidence

- At least eight attack classes have automated regressions.
- The Kubernetes/gVisor environment is versioned and independently identifiable.
- Tests validate timeout/process cleanup, output truncation, artifact path safety, and no residual processes.
- Threats map to tests and residual risks are documented.
- Mac or default-container results are never presented as gVisor security evidence.

### Not in scope

- Claims of absolute sandbox security.
- A custom container runtime.
- Firecracker unless the threat model demonstrates the need for a microVM boundary.

## v0.4 — Durable Control Plane

### Outcome

Create, inspect, cancel, resume, and replay evaluation runs that survive process failure without duplicate final results.

### Scope

- Typed API and CLI commands for run lifecycle operations.
- PostgreSQL-backed `EvalRun`, task, attempt, result, and artifact metadata.
- Immutable commands and explicit run state transitions.
- Queue claim, lease, heartbeat, expiry, and reclaim semantics.
- Caller-provided idempotency keys and same-key/different-intent rejection.
- Content-addressed artifacts in an S3-compatible store.
- Budget estimation and admission inputs.

### Exit evidence

- Restarting the control plane does not lose authoritative run state.
- Killing a worker causes safe task takeover.
- Repeated final commits produce one authoritative result.
- Late arrivals and same-key/different-payload requests are tested.
- Model, user code, verifier, sandbox, scheduler, and storage failures remain distinguishable.
- An architecture document and recovery demo are published.

### Not in scope

- Kafka or Temporal introduced only to avoid defining state semantics.
- Exactly-once execution claims.
- Dashboard-first development.

## v0.5 — Distributed Executor

### Outcome

Run the same manifest locally and through KubeRay while keeping work bounded and durable results correct during worker, actor, and storage failures.

### Scope

- Ray Data for versioned dataset ingestion, normalization, and sharding.
- Ray Core for stateful orchestration and task/actor execution.
- Bounded in-flight scheduling with `ray.wait` or an equivalent mechanism.
- CPU verifier, GPU inference, and external API resource pools.
- KubeRay RayJob deployment and cleanup.
- Failure injection for task/actor crash, large objects, stragglers, and transient storage failure.

### Exit evidence

- The same manifest runs locally and as a RayJob.
- Worker/actor failure does not lose committed results or create duplicate final results.
- 1/2/4/8/16 concurrency experiments report throughput, P95, memory, spill, and recovery.
- A Ray Data versus Ray Core ADR is published.
- At least one component is deliberately not implemented with Ray, with rationale.

### Not in scope

- Unbounded driver submission.
- Ray source changes without an isolated framework defect and minimal reproduction.
- Treating Ray retries as business idempotency.

## v0.6 — Reliability & Evaluation Evidence

### Outcome

Operators can trace failures across the system, determine the active bottleneck, and know when a benchmark conclusion is statistically or operationally invalid.

### Scope

- OpenTelemetry traces, metrics, and log correlation.
- Run → task → model/sandbox/commit span hierarchy.
- Queue, model, sandbox, storage, and commit latency decomposition.
- SLI/SLO definitions for availability, infrastructure error, latency, replay consistency, and cost.
- pass@1/pass@k, confidence intervals, paired deltas, and infrastructure exclusion policy.
- Capacity and chaos experiments.

### Exit evidence

- A failed task links to attempt metadata, trace, sandbox logs, manifest, and artifacts.
- Final success and first-attempt success are reported separately.
- Retry amplification and infrastructure error rates are visible.
- Reports automatically mark runs partial or invalid when exclusion thresholds are exceeded.
- Capacity and chaos reports include environment, versions, sample sizes, hypotheses, findings, and limitations.

### Not in scope

- A polished dashboard without traceable underlying evidence.
- A single composite score hiding reliability, cost, and capability tradeoffs.

## v0.7 — Reward Runtime

### Outcome

veRL can request deterministic verifier rewards asynchronously without uncontrolled backlog, duplicate billing, or inconsistent retries.

### Scope

- Async reward request/result contracts.
- veRL Reward Loop adapter using the VeriRun reward gateway.
- Concurrency limits, timeouts, cancellation policy, retry policy, backpressure, and idempotency.
- Version-aware reward cache keys.
- Mock rollout and deterministic verifier tests before model training.
- Small-model/small-data end-to-end smoke.
- Frozen EvalPlus held-out evaluation outside the training loop.

### Exit evidence

- Bounded reward calls remain stable under interruption and retry.
- Repeated rollout submission does not produce inconsistent reward.
- Reward results replay to the same verifier/test/image identity.
- Training reward and held-out evaluation are reported separately.
- Failure and timeout behavior is not silently converted into zero reward.

### Not in scope

- A new training framework.
- Model-quality improvement as the first acceptance criterion.
- Permanent caching that ignores verifier environment changes.

## v0.8 — Agent Workloads (optional)

### Outcome

Ingest Harbor/Terminal-Bench workloads while preserving environment-build, agent-rollout, and verifier-stage attribution.

### Scope

- Harbor adapter through public interfaces; no fork of Harbor core.
- Terminal-Bench 2.0 oracle run.
- Explicitly versioned 10–20 task subset for an actual agent smoke.
- Unified artifacts and reports across the three execution stages.

### Exit evidence

- Environment, agent, and verifier time/errors are reported separately.
- Subset results are never presented as full leaderboard results.
- Adapter lineage resolves upstream task and environment versions.
- Retry policy is stage-aware.

### Not in scope

- Complete SWE-bench.
- A general agent platform.
- Rewriting Harbor or Terminal-Bench harness logic.

This milestone is the first to be deferred when maintainer capacity is constrained.

## v1.0 — Stable Runtime

v1.0 is not a calendar target. It becomes eligible only after the project can support a documented compatibility contract and reproduce its core guarantees.

Minimum eligibility:

- protocol and artifact formats have an explicit compatibility policy;
- the supported workload/version matrix is published;
- local and KubeRay execution use the same manifest semantics;
- the Kubernetes/gVisor attack suite passes on a documented environment;
- recovery and idempotency tests cover worker, control-plane, and storage failures;
- observability and statistical reports can invalidate bad runs automatically;
- the async reward path is reproducible and bounded;
- installation, upgrade, rollback, and known-limitations documentation exists;
- at least one tagged release has been independently reproduced from its published instructions.

## Cross-cutting tracks

These tracks are owned by every milestone rather than postponed to the end.

### Compatibility and reproducibility

- Lock Python and dependency versions.
- Record benchmark release/commit and dataset hashes.
- Record model and tokenizer revisions.
- Pin or record container image digests.
- Publish a compatibility matrix with every release.

### Security

- Keep secrets and credentials out of manifests and artifacts.
- Use private disclosure for security reports.
- Threat-model every new execution backend.
- Treat external benchmark data, model output, and artifacts as untrusted inputs.
- Add dependency and secret scanning before executable releases.

### Documentation

- Architecture overview and contracts.
- ADRs for consequential choices.
- Reproduction commands and environment specifications.
- Evidence reports with limitations, not screenshots alone.
- Operator troubleshooting and contributor development guides.

### Engineering quality

- Formatting, linting, typing, and tests in GitHub Actions.
- Unit, integration, attack, recovery, load, and chaos test layers.
- No merge to `main` with required checks failing.
- Release notes generated from reviewed issue/PR metadata, then edited for accuracy.

## GitHub execution model

### Milestones

Create one GitHub milestone for Foundation and each planned release. Milestone descriptions should link to the matching roadmap anchor and list the exit evidence. A milestone is not closed because all code issues are merged; evidence and documentation issues must also be complete.

Recommended milestone names:

- `Foundation`
- `v0.1 Protocol Baseline`
- `v0.2 Async Model Gateway`
- `v0.3 Isolated Execution`
- `v0.4 Durable Control Plane`
- `v0.5 Distributed Executor`
- `v0.6 Reliability & Evaluation Evidence`
- `v0.7 Reward Runtime`
- `v0.8 Agent Workloads`

### Labels

Use a small, composable taxonomy rather than dozens of overlapping labels.

**Area**

- `area: protocol`
- `area: gateway`
- `area: sandbox`
- `area: control-plane`
- `area: ray`
- `area: observability`
- `area: reporting`
- `area: reward`
- `area: agent`
- `area: docs`
- `area: github`

**Type**

- `type: feature`
- `type: bug`
- `type: design`
- `type: experiment`
- `type: docs`
- `type: test`
- `type: security`

**Priority and flow**

- `priority: critical`
- `priority: high`
- `priority: normal`
- `status: needs-design`
- `status: blocked`
- `good first issue`
- `help wanted`

Avoid a permanent `in progress` issue label if the GitHub project board already owns workflow state.

### Issue contract

Every implementation or experiment issue should state:

1. the problem or invariant at risk;
2. scope and explicit non-scope;
3. acceptance criteria;
4. required tests and evidence artifacts;
5. compatibility or migration impact;
6. security and failure-mode considerations;
7. links to the roadmap milestone and relevant ADR.

Large issues that cannot be independently verified should be split before entering `Ready`.

### Pull-request contract

Every pull request should:

- link the issue it advances;
- explain the behavioral contract changed;
- include tests proportional to risk;
- update user/operator/contributor documentation when relevant;
- identify new dependencies or compatibility changes;
- state which evidence was generated and where it is stored;
- avoid unsupported benchmark, security, reliability, or performance claims.

### Project board

Recommended workflow columns:

1. Inbox
2. Design
3. Ready
4. In progress
5. Review
6. Verification
7. Done

An item enters `Verification` when implementation is merged or review-ready but exit evidence is still being reproduced. This prevents “code merged” from being mistaken for “milestone proven.”

### Releases

Every release should include:

- supported environments and compatibility matrix;
- included and excluded capabilities;
- reproducible setup and run instructions;
- test/evidence summary with links to immutable artifacts;
- security assumptions and known limitations;
- upgrade or migration notes;
- benchmark protocol details for any reported score.

## Active issue map

The initial independently reviewable GitHub issues are:

1. [#1 — Complete GitHub repository foundation](https://github.com/aaron-for-value/VeriRun/issues/1)
2. [#2 — Define v0.1 protocol schemas and canonical hashing](https://github.com/aaron-for-value/VeriRun/issues/2)
3. [#3 — Pin EvalPlus and publish v0.1 compatibility](https://github.com/aaron-for-value/VeriRun/issues/3)
4. [#4 — Implement v0.1 executor boundary and structured verification](https://github.com/aaron-for-value/VeriRun/issues/4)
5. [#5 — Implement content-addressed artifacts and verify/replay/report CLI](https://github.com/aaron-for-value/VeriRun/issues/5)
6. [#6 — Add v0.1 CI and publish baseline replay evidence](https://github.com/aaron-for-value/VeriRun/issues/6)

Implementation remains in `Verification` until the linked pull request reproduces the milestone evidence in CI.

## Scope reduction order

If time, compute, or infrastructure is constrained, reduce scope in this order:

1. defer v0.8 agent workloads;
2. reduce optional LiveCodeBench coverage while keeping a fixed version;
3. reduce deployment polish and dashboard breadth;
4. use frozen subsets for development smoke while preserving accurate labels;
5. keep v0.1 protocol/replay, v0.2 bounded async behavior, v0.4 recovery/idempotency, and v0.7 reward-path evidence.

Correctness claims, lineage, honest reporting, and security boundaries are not scope-reduction candidates.

## Roadmap change policy

Roadmap changes are expected, but they must be explicit:

- scope additions require a concrete user/operator problem and acceptance evidence;
- milestone reordering must state which dependency assumption changed;
- removed work remains visible in issue history or release notes;
- completed gates are not rewritten to make historical evidence appear stronger;
- a technology is adopted because it solves a measured problem, not because its name improves the architecture diagram.
