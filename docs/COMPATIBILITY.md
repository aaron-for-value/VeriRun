# Compatibility Matrix

VeriRun records exact versions in evidence artifacts. This document lists supported development combinations rather than floating “latest” dependencies.

## Current development matrix

| Component | Version / identity | Status | Notes |
|---|---|---|---|
| Python | 3.12.x | Supported | CI and release evidence target Python 3.12 only. |
| Pydantic | >=2.10,<3 | Supported | Core protocol runtime dependency. |
| EvalPlus | v0.3.1 | Adapter target | Tag commit `e5d0ed0bab96280b60b637ec7f15b5e4841b0cb2`. |
| HumanEval+ dataset | v0.1.10 + runtime API MD5 | Evidence-bound | Exact algorithm-qualified hash is captured from `get_human_eval_plus_hash`. |
| MBPP+ dataset | v0.2.0 + runtime API MD5 | Evidence-bound | Exact algorithm-qualified hash is captured from `get_mbpp_plus_hash`; full M0 evidence covers the ordered 378-task workload. |
| Local executor | `trusted-fixtures-only` | Development only | Not a security boundary. |
| Container executor | `development-container` + digest-pinned image | Released in v0.3.0 | Docker development tier only; no network, read-only runner mount, non-root, capability drop, and explicit resource limits. Not gVisor evidence. |
| macOS arm64 | Local development | Supported for protocol work | Not valid gVisor security evidence. |
| Kubernetes + gVisor | local single-node kind, `runsc` | Released in v0.3.0 | Evidence is limited to the recorded local kind environment; no general cluster security claim. |
| PostgreSQL | 16.x; smoke recorded on 16.13 | Released in v0.4.0 | Authoritative v0.4 plan/run/task/attempt/result/command/artifact metadata. HA/failover is not evidenced. |
| Psycopg | 3.3.5 | v0.4.0 lock | PostgreSQL client; binary distribution is used by the reproducible development environment. |
| S3-compatible store | MinIO local fixture | Released in v0.4.0 | Content-addressed byte storage and round-trip integrity only; production object-store compatibility is not claimed. |
| MinIO Python SDK | 7.2.20 | v0.4.0 lock | Used by the S3-compatible adapter and local recovery smoke. |

## Compatibility rules

- The package rejects Python versions outside `>=3.12,<3.13`.
- A benchmark release, dataset hash, prompt protocol, or verifier change creates a distinct result lineage.
- A verifier graph, image/config digest, required-evidence policy, aggregation policy, or frozen-plan change creates a distinct v0.4 comparison cohort.
- A replay may use a new attempt ID and timestamps, but semantic identity must remain stable.
- Floating benchmark branches are not accepted in release evidence.
- Dependency lock snapshots are regenerated intentionally and reviewed as code changes.
- `requirements/core-dev.lock.txt` is the cross-platform Python 3.12 quality-gate snapshot; the larger EvalPlus environment is captured separately because it is optional.
- `requirements/evalplus-v0.1.lock.txt` captures the resolved v0.1 adapter environment, including EvalPlus's generation-client dependencies, to avoid repeated resolver drift and backtracking.
- EvalPlus v0.3.1's HumanEval+ hash helper resolves its bundled default dataset even when passed another version; VeriRun records this limitation and does not claim arbitrary-version verification through that helper.
- EvalPlus v0.3.1's memory `setrlimit` path fails on the supported Darwin environment. The deterministic built-in smoke may use `EVALPLUS_MAX_MEMORY_BYTES=-1`, records that exception in evidence, and remains trusted-fixture compatibility evidence only. Linux keeps the upstream default.

## Known limitations

- EvalPlus brings a larger optional dependency tree than the VeriRun core package.
- The local executor is limited to trusted fixtures.
- v0.1-v0.3 execution paths retain the local content-addressed artifact store. The v0.4 control plane adds an S3-compatible adapter, but has only local MinIO evidence.
- Result authenticity and signing are not implemented.
- The supported workload smoke is a labeled subset, not a leaderboard score.
- The full M0 fixture evidence is documented separately in `docs/M0_EVALPLUS_EVIDENCE.md`; it remains reproducibility evidence for deterministic candidates, not a model-quality claim.
- M3 has no HTTP/authentication layer, database failover evidence, object-store outage chaos, distributed scheduler, or exactly-once execution claim.
