# VeriRun v0.5 Distributed Executor Evidence

> This report covers CPU trusted fixtures only. It is not a benchmark score,
> capacity study, GPU/API availability claim, or production-reliability claim.

## Source and runtime

- Implementation revision: `2c2390107887f82c4e955bb7e89d8d23dd57665b`
- Local Ray: Python `3.12.13`, Ray `2.50.1`, macOS arm64
- KubeRay reference: local single-node `kind-verirun-m2`, Kubernetes `v1.37.0`,
  Debian 13 arm64, kernel `6.8.0-100-generic`, containerd `2.3.4`
- KubeRay operator: `quay.io/kuberay/operator:v1.4.0`
- Fixture image: `sha256:dd24800bdb5d29c5cf9383f9e2d67c6952217697cda246108ec55f3fcfcc9a7a`
  (258,940,017 bytes), bound at build time to the implementation revision above

[`runtime.json`](runtime.json) contains the complete runtime record. Every
machine-readable summary below records the same clean implementation revision.

## Frozen-plan replay

The local replay and two independently created KubeRay RayJobs each ran the same
three-candidate CPU trusted fixture. All three summaries record the plan digest
`de091905c693bb712d9a69bcf53c9d4789b78e94b7bb78732c34ca864e8bdabc` and pass:

- baseline and replay completed;
- Ray Data candidate rows match;
- frozen plan digest is unchanged;
- all final commits were inserted once; and
- all trusted fixture results are `passed`.

Artifacts: [local replay](local-replay/summary.json),
[KubeRay replay 1](kuberay/replay-1-summary.json), and
[KubeRay replay 2](kuberay/replay-2-summary.json).

The RayJobs were `verirun-m4-trusted-fixtures-9jgsj` and
`verirun-m4-trusted-fixtures-h49zn`. Both reached `SUCCEEDED`, then their RayJob
CRs were explicitly deleted. The associated RayCluster, head, and worker pods
were verified absent before the next job.

## Fault recovery

`verirun-m4-faults-v2jc5` reached `SUCCEEDED` with all seven checks true:

- task crash recovered through lease reclaim and a new attempt;
- actor crash recovered through lease reclaim and a new attempt;
- one transient driver-side final-commit failure recovered through lease reclaim;
- the deterministic straggler completed with its 0.2-second delay intact;
- six 40,000,000-byte payloads were returned;
- all configured Ray metrics endpoints were scraped; and
- actual spill was observed: `240000018` bytes on one Ray node.

The [fault summary](kuberay/fault-summary.json) records the attempt errors as
reclaimable, every recovered run as completed with three results under the frozen
plan digest, and the raw per-node spill observations. The RayJob and all Ray
infrastructure were explicitly deleted after capture.

## Logical concurrency fixture

The local 16-task, fixed-20ms fixture passed all contract checks at every requested
logical concurrency. These measurements describe scheduler behavior in this exact
development environment; they are not CPU capacity measurements.

| Logical concurrency | Elapsed ms | Throughput tasks/s | P95 worker ms | Driver max RSS bytes |
|---:|---:|---:|---:|---:|
| 1 | 671 | 23.816 | 21 | 159301632 |
| 2 | 323 | 49.432 | 21 | 163201024 |
| 4 | 240 | 66.635 | 21 | 165281792 |
| 8 | 207 | 77.241 | 21 | 168755200 |
| 16 | 372 | 42.917 | 21 | 172244992 |

Every row reports 16 completed tasks, one effective final commit per task, zero
recovery events, and zero spill bytes. The throughput reduction at logical 16 is
retained as an observation, not explained as a capacity conclusion. See the
[full concurrency summary](concurrency/summary.json).

## Limits

- The environment is one local arm64 kind node, not a multi-node or autoscaling
  KubeRay deployment.
- CPU trusted fixtures are the only live workload. GPU inference and external API
  pools are named-resource admission contracts, not live hardware/provider evidence.
- No long-duration soak, HA, cross-cluster recovery, external provider outage, or
  production operational reliability claim is made.
- Ray never chooses verifiers, recompiles verification plans, aggregates results, or
  writes durable control-plane state; those authorities remain in M3.
