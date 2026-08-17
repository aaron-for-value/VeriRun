# Async Model Gateway (v0.2)

`AsyncModelGateway` is the v0.2 admission and transport boundary for candidate
generation against an OpenAI-compatible `POST /v1/chat/completions` endpoint.
It is deliberately separate from verification: a generation result records model
transport behavior and completion text; v0.1 verification replay consumes frozen
candidates and never calls a model endpoint.

## Contract and ownership

- `GatewayConfig` holds non-secret endpoint resource policy. API keys are supplied
  only to `AsyncModelGateway(...)` and are not placed in generation results or smoke
  reports.
- `GenerationRequest` holds one task ID, prompt, immutable model identity, and
  sampling settings.
- `GenerationResult` records the terminal status, attempts, latency, completion
  texts, final error class, and the ordered retry-error history.
- The gateway owns a single reusable `httpx.AsyncClient` for its lifetime. It closes
  that client through `aclose()` or the async context manager.

The public validation schemas are exported under `schemas/`; they do not constitute
a provider-specific capability claim.

## Admission, timeout, and retry policy

Each request acquires three independent local controls before it is sent:

1. a concurrency semaphore;
2. a monotonic QPS reservation; and
3. a reservation for `GenerationSpec.max_tokens` from the in-flight token budget.

`generate_many()` uses a bounded `asyncio.Queue` and at most
`max_concurrency` workers. It does not create one task per item. A request larger
than the configured token budget is rejected before transport.

HTTPX has separate connect, read, write, and pool timeouts. Parent cancellation is
propagated rather than converted into a false successful result; producer and worker
tasks are cancelled and awaited before the cancellation escapes. Token reservations
and concurrency slots are released in `finally` blocks.

Only 429, 5xx, timeout, and transport failures consume the bounded retry budget.
Retry delay uses capped full jitter. Other 4xx responses and malformed successful
responses are terminal. `attempt_error_classes` preserves the actual root causes
that led to a retry instead of replacing them with a generic final error.

## Verification boundary

Generation can be repeated by an endpoint provider and transport failures can be
ambiguous after a request leaves the client. v0.2 therefore makes no exactly-once
generation claim. It records a caller-provided `request_id`, retry attempts, and
failure history. Durable idempotency, leases, and final-result commit belong to the
v0.4 control plane.

## Reproducible local evidence

Run the deterministic fake-server exercise without an API key:

```bash
./.venv/bin/python -m verirun gateway-smoke --output evidence/v0.2/gateway-smoke
```

It covers 429, 5xx, slow response, disconnect, malformed JSON, bounded
concurrency, retry amplification, and a small comparison of sequential, eager-task,
and bounded-worker submission. The test suite uses the same server for parent
cancellation and timeout-cleanup checks. The comparison reports throughput,
admitted-request P95, and Python traced-allocation peak for the recorded machine;
it is not a model or provider performance benchmark.

## Non-goals

- distributed/global rate limiting;
- provider-specific request routing or authentication discovery;
- exactly-once model generation;
- CPU parallelism claims from `asyncio`; and
- hostile-code execution or sandboxing.
