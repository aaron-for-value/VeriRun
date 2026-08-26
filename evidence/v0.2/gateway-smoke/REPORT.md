# VeriRun v0.2 Async Gateway Smoke Report

> This report uses a local fake OpenAI-compatible server. It validates gateway
> control-flow and resource bounds; it is not model performance evidence.

- Python: `3.12.13`
- Platform: `macOS-26.5.2-arm64-arm-64bit`
- Source revision: `dab3c5274bd102a4eed81f39f8a046af09d50da0`
- Working tree clean at start: `True`

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
| sequential | 324 | 73.91 | 14 | 443234 |
| eager_tasks | 101 | 235.93 | 100 | 532774 |
| bounded_workers | 94 | 254.02 | 16 | 506769 |

## Limitations

- This deterministic local-fake-server smoke is not a provider compatibility claim.
- The watchdog samples one local event loop. It distinguishes this gateway's
  awaited I/O from a deliberate synchronous block; it is not a host-wide
  scheduling or latency-service-level guarantee.
- The comparison measures Python allocation tracing, not process RSS, and is only
  directional for this machine and local workload.
