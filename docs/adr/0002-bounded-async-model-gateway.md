# ADR 0002: Bounded async model gateway

- Status: Accepted for v0.2
- Date: 2026-08-17
- Owners: VeriRun maintainers
- Related milestone: v0.2 Async Model Gateway

## Context

Model calls have provider quotas, variable latency, transient failures, and must be
cancellable. Creating a task for every input makes memory and cancellation behavior
depend on batch size. A generic retry helper also loses the distinction between a
provider rate limit, server error, timeout, malformed response, and caller error.

## Decision

v0.2 provides an OpenAI-compatible async gateway with:

- one reusable HTTPX `AsyncClient` per gateway lifetime and explicit close;
- independent local concurrency, QPS, and output-token admission controls;
- a bounded producer/consumer queue for batch submission;
- separate connect, read, write, and connection-pool timeouts;
- a bounded full-jitter retry policy only for 429, 5xx, timeout, and transport
  faults;
- structured terminal error classes plus ordered failed-attempt history; and
- a deterministic local fake server for failure and cancellation evidence.

Cancellation remains a cancellation signal to the parent. `generate_many()` cancels
and awaits internal producer and worker tasks before propagating either parent
cancellation or a `GatewayBatchError` in fail-fast mode.

## Alternatives considered

### Create a task per input

Rejected because memory and cancellation fan-out grow with batch size even when
actual socket concurrency is capped.

### One global limiter for all controls

Rejected because request rate, concurrent sockets, and anticipated output tokens are
different scarce resources with different failure modes.

### Retry every error

Rejected because malformed output and most client errors will not become valid by
retrying, and indiscriminate retries hide the root cause and amplify outages.

## Consequences

- v0.2 is local-process policy only; distributed admission needs durable state in a
  later milestone.
- A transport failure after request submission is still ambiguous, so v0.2 makes no
  exactly-once generation guarantee.
- The fake-server report is a control-flow and resource-bound check, not a provider
  compatibility or model-performance result.
