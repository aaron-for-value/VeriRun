# VeriRun v0.2 Async Gateway Smoke Report

> This report uses a local fake OpenAI-compatible server. It validates gateway
> control-flow and resource bounds; it is not model performance evidence.

- Python: `3.12.13`
- Platform: `macOS-26.5.2-arm64-arm-64bit`
- Source revision: `d6021141fd6bdff6c6366fa7a0b593f612146354`
- Working tree clean at start: `True`

| Scenario | Evidence |
|---|---|
| 429 retry | 2 attempts |
| 5xx retry | 2 attempts |
| Retry root causes | rate_limited, server_error |
| Slow response | succeeded |
| Disconnect retry | 2 attempts |
| Malformed JSON | malformed_response |
| Bounded fake-server concurrency | 2 / 2 |

## Submission comparison

The same local 24-request workload (10 ms scripted response) was run three times.
P95 is from admitted HTTP requests; it excludes time waiting in the bounded queue.

| Submission mode | Duration (ms) | Throughput (req/s) | Admitted P95 (ms) | Peak traced memory (bytes) |
|---|---:|---:|---:|---:|
| sequential | 302 | 79.46 | 12 | 447415 |
| eager_tasks | 85 | 279.8 | 84 | 551875 |
| bounded_workers | 78 | 306.44 | 14 | 549085 |

## Limitations

- This deterministic local-fake-server smoke is not a provider compatibility claim.
- The comparison measures Python allocation tracing, not process RSS, and is only
  directional for this machine and local workload.
