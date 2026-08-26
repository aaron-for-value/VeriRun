# VeriRun v0.2 Async Gateway Smoke Report

> This report uses a local fake OpenAI-compatible server. It validates gateway
> control-flow and resource bounds; it is not model performance evidence.

- Python: `3.12.13`
- Platform: `macOS-26.5.2-arm64-arm-64bit`
- Source revision: `74d47fb59d6a6c12867a989b3d9ac2fe607f9066`
- Working tree clean at start: `False`

| Scenario | Evidence |
|---|---|
| 429 retry | 2 attempts |
| 5xx retry | 2 attempts |
| Retry root causes | rate_limited, server_error |
| Slow response | succeeded |
| Slow upstream event-loop lag | 1 ms / 100 ms |
| Fast request while slow upstream pending | True |
| Controlled block detected | True (116 ms / 100 ms) |
| Disconnect retry | 2 attempts |
| Malformed JSON | malformed_response |
| Bounded fake-server concurrency | 2 / 2 |

## Submission comparison

The same local 24-request workload (10 ms scripted response) was run three times.
P95 is from admitted HTTP requests; it excludes time waiting in the bounded queue.

| Submission mode | Duration (ms) | Throughput (req/s) | Admitted P95 (ms) | Peak traced memory (bytes) |
|---|---:|---:|---:|---:|
| sequential | 324 | 73.85 | 14 | 444439 |
| eager_tasks | 103 | 232.55 | 101 | 544892 |
| bounded_workers | 89 | 267.87 | 16 | 551962 |

## Limitations

- This deterministic local-fake-server smoke is not a provider compatibility claim.
- The watchdog samples one local event loop. It distinguishes this gateway's
  awaited I/O from a deliberate synchronous block; it is not a host-wide
  scheduling or latency-service-level guarantee.
- The comparison measures Python allocation tracing, not process RSS, and is only
  directional for this machine and local workload.
