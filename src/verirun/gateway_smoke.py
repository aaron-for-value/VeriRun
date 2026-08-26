"""Local fake OpenAI server and reproducible v0.2 gateway smoke evidence."""

from __future__ import annotations

import asyncio
import json
import platform
import tracemalloc
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

from verirun.canonical import write_canonical_json
from verirun.gateway import (
    AsyncModelGateway,
    GatewayConfig,
    GenerationRequest,
    GenerationStatus,
)
from verirun.models import GenerationSpec, ModelSpec
from verirun.provenance import source_state


@dataclass(frozen=True)
class ScriptedResponse:
    """One deterministic response emitted by :class:`ScriptedOpenAIServer`."""

    status_code: int = 200
    body: Mapping[str, object] | None = None
    raw_body: str | None = None
    delay_seconds: float = 0.0
    disconnect: bool = False


class EventLoopWatchdog:
    """Measure event-loop scheduling lag during a bounded local scenario."""

    def __init__(
        self,
        *,
        sample_interval_seconds: float = 0.01,
        detection_threshold_seconds: float = 0.1,
    ) -> None:
        self._sample_interval_seconds = sample_interval_seconds
        self._detection_threshold_seconds = detection_threshold_seconds
        self._heartbeat_count = 0
        self._max_lag_seconds = 0.0
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("event-loop watchdog is already running")
        self._task = asyncio.create_task(self._sample(), name="verirun-event-loop-watchdog")
        await asyncio.sleep(0)

    async def stop(self) -> dict[str, int | bool]:
        if self._task is None:
            raise RuntimeError("event-loop watchdog has not started")
        self._stop.set()
        await self._task
        self._task = None
        max_lag_ms = round(self._max_lag_seconds * 1_000)
        threshold_ms = round(self._detection_threshold_seconds * 1_000)
        return {
            "heartbeat_count": self._heartbeat_count,
            "max_lag_ms": max_lag_ms,
            "threshold_ms": threshold_ms,
            "block_detected": max_lag_ms >= threshold_ms,
        }

    async def _sample(self) -> None:
        loop = asyncio.get_running_loop()
        expected_at = loop.time() + self._sample_interval_seconds
        while not self._stop.is_set():
            await asyncio.sleep(max(0.0, expected_at - loop.time()))
            observed_at = loop.time()
            self._max_lag_seconds = max(self._max_lag_seconds, observed_at - expected_at)
            self._heartbeat_count += 1
            expected_at += self._sample_interval_seconds


class ScriptedOpenAIServer:
    """Minimal local OpenAI-compatible server for deterministic fault scenarios."""

    def __init__(self, plans: Mapping[str, Sequence[ScriptedResponse]]) -> None:
        self._plans = {prompt: deque(responses) for prompt, responses in plans.items()}
        self._server: asyncio.AbstractServer | None = None
        self._base_url: str | None = None
        self._active_requests = 0
        self.max_active_requests = 0
        self.request_counts: dict[str, int] = defaultdict(int)
        self.request_started_at: dict[str, list[float]] = defaultdict(list)

    @property
    def active_requests(self) -> int:
        return self._active_requests

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            raise RuntimeError("server has not started")
        return self._base_url

    async def __aenter__(self) -> ScriptedOpenAIServer:
        self._server = await asyncio.start_server(self._handle_connection, host="127.0.0.1", port=0)
        sockets = getattr(self._server, "sockets", None)
        if not sockets:
            raise RuntimeError("server did not expose a listening socket")
        host, port = sockets[0].getsockname()[:2]
        self._base_url = f"http://{host}:{port}"
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._base_url = None

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._active_requests += 1
        self.max_active_requests = max(self.max_active_requests, self._active_requests)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            content_length = _content_length(headers)
            body = await reader.readexactly(content_length)
            prompt = _prompt_from_body(body)
            self.request_counts[prompt] += 1
            self.request_started_at[prompt].append(asyncio.get_running_loop().time())
            plan = self._plans.get(prompt)
            if not plan:
                response = ScriptedResponse(status_code=404, body={"error": "no scripted response"})
            else:
                response = plan.popleft()
            if response.delay_seconds:
                await asyncio.sleep(response.delay_seconds)
            if response.disconnect:
                return
            raw_body = response.raw_body
            if raw_body is None:
                raw_body = json.dumps(response.body or _completion(f"completion:{prompt}"))
            reason = "OK" if response.status_code < 400 else "ERROR"
            encoded_body = raw_body.encode("utf-8")
            writer.write(
                (
                    f"HTTP/1.1 {response.status_code} {reason}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(encoded_body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                + encoded_body
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._active_requests -= 1
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()


def _content_length(headers: bytes) -> int:
    for line in headers.decode("iso-8859-1").split("\r\n"):
        if line.lower().startswith("content-length:"):
            return int(line.partition(":")[2].strip())
    raise ValueError("request has no content length")


def _prompt_from_body(body: bytes) -> str:
    payload = json.loads(body)
    messages = payload["messages"]
    return str(messages[0]["content"])


def _completion(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


def _request(prompt: str, *, max_tokens: int = 8) -> GenerationRequest:
    return GenerationRequest(
        request_id=f"request-{prompt}",
        task_id=f"task-{prompt}",
        prompt=prompt,
        model=ModelSpec(endpoint_type="openai-compatible", model_revision="fake-model-v1"),
        generation=GenerationSpec(max_tokens=max_tokens),
    )


async def _run_gateway_smoke_async() -> dict[str, Any]:
    plans = {
        "retry-429": [ScriptedResponse(status_code=429), ScriptedResponse()],
        "retry-500": [ScriptedResponse(status_code=500), ScriptedResponse()],
        "slow": [ScriptedResponse(delay_seconds=0.03)],
        "disconnect": [ScriptedResponse(disconnect=True), ScriptedResponse()],
        "malformed": [ScriptedResponse(raw_body="{")],
        **{f"batch-{index}": [ScriptedResponse(delay_seconds=0.02)] for index in range(6)},
    }
    async with ScriptedOpenAIServer(plans) as server:
        config = GatewayConfig(
            base_url=server.base_url,
            max_concurrency=2,
            requests_per_second=1_000,
            max_in_flight_tokens=16,
            queue_capacity=2,
            read_timeout_seconds=0.5,
            max_attempts=2,
            retry_base_delay_seconds=0,
            retry_max_delay_seconds=0,
        )
        async with AsyncModelGateway(config) as gateway:
            retry_429, retry_500, slow, disconnect, malformed = await gateway.generate_many(
                [
                    _request(name)
                    for name in ("retry-429", "retry-500", "slow", "disconnect", "malformed")
                ]
            )
            started_at = perf_counter()
            batch = await gateway.generate_many([_request(f"batch-{index}") for index in range(6)])
            batch_duration_ms = int((perf_counter() - started_at) * 1_000)

    event_loop = await _run_event_loop_evidence_async()

    return {
        "schema_version": "verirun.gateway-smoke-report/v2",
        "generated_at": datetime.now(UTC),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "implementation": platform.python_implementation(),
        },
        "source": source_state(),
        "scenarios": {
            "retry_429_attempts": retry_429.attempts,
            "retry_429_error_history": [item.value for item in retry_429.attempt_error_classes],
            "retry_500_attempts": retry_500.attempts,
            "retry_500_error_history": [item.value for item in retry_500.attempt_error_classes],
            "slow_status": slow.status.value,
            "disconnect_attempts": disconnect.attempts,
            "malformed_status": malformed.status.value,
            "malformed_error_class": malformed.error_class.value if malformed.error_class else None,
        },
        "backpressure": {
            "configured_concurrency": config.max_concurrency,
            "queue_capacity": config.queue_capacity,
            "max_fake_server_active_requests": server.max_active_requests,
            "batch_duration_ms": batch_duration_ms,
            "batch_succeeded": all(result.status is GenerationStatus.SUCCEEDED for result in batch),
        },
        "request_counts": dict(sorted(server.request_counts.items())),
        "event_loop": event_loop,
    }


async def _run_event_loop_evidence_async() -> dict[str, Mapping[str, int | bool | str]]:
    """Distinguish non-blocking slow I/O from a detectable synchronous block."""

    async with (
        ScriptedOpenAIServer(
            {
                "slow-upstream": [ScriptedResponse(delay_seconds=0.12)],
                "fast-during-slow": [ScriptedResponse()],
            }
        ) as server,
        AsyncModelGateway(
            GatewayConfig(
                base_url=server.base_url,
                max_concurrency=2,
                requests_per_second=1_000,
                max_in_flight_tokens=16,
                queue_capacity=2,
                read_timeout_seconds=0.5,
                max_attempts=1,
            )
        ) as gateway,
    ):
        watchdog = EventLoopWatchdog()
        await watchdog.start()
        slow_task = asyncio.create_task(gateway.generate_one(_request("slow-upstream")))
        while server.active_requests == 0:
            await asyncio.sleep(0.001)
        fast = await gateway.generate_one(_request("fast-during-slow"))
        fast_completed_while_slow_pending = not slow_task.done()
        slow = await slow_task
        slow_upstream = await watchdog.stop()

    blocking_watchdog = EventLoopWatchdog()
    await blocking_watchdog.start()
    await asyncio.sleep(0.02)
    sleep(0.12)
    await asyncio.sleep(0.02)
    controlled_block = await blocking_watchdog.stop()

    return {
        "slow_upstream": {
            **slow_upstream,
            "slow_status": slow.status.value,
            "fast_status": fast.status.value,
            "fast_completed_while_slow_pending": fast_completed_while_slow_pending,
        },
        "controlled_block": controlled_block,
    }


async def _measure_submission_mode(mode: str) -> dict[str, object]:
    request_count = 24
    plans = {
        f"{mode}-{index}": [ScriptedResponse(delay_seconds=0.01)] for index in range(request_count)
    }
    async with (
        ScriptedOpenAIServer(plans) as server,
        AsyncModelGateway(
            GatewayConfig(
                base_url=server.base_url,
                max_concurrency=4,
                requests_per_second=1_000,
                max_in_flight_tokens=128,
                queue_capacity=4,
                max_attempts=1,
            )
        ) as gateway,
    ):
        requests = [_request(f"{mode}-{index}") for index in range(request_count)]
        tracemalloc.start()
        started_at = perf_counter()
        if mode == "sequential":
            results = [await gateway.generate_one(request) for request in requests]
        elif mode == "eager_tasks":
            results = list(
                await asyncio.gather(*(gateway.generate_one(request) for request in requests))
            )
        elif mode == "bounded_workers":
            results = list(await gateway.generate_many(requests))
        else:
            raise ValueError(f"unknown submission mode: {mode}")
        elapsed_seconds = perf_counter() - started_at
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    latencies = sorted(result.latency_ms for result in results)
    p95_index = max(0, int(len(latencies) * 0.95 + 0.999_999) - 1)
    return {
        "mode": mode,
        "request_count": request_count,
        "duration_ms": int(elapsed_seconds * 1_000),
        "throughput_requests_per_second": round(request_count / elapsed_seconds, 2),
        "admitted_request_p95_ms": latencies[p95_index],
        "peak_tracemalloc_bytes": peak_bytes,
        "max_fake_server_active_requests": server.max_active_requests,
        "all_succeeded": all(result.status is GenerationStatus.SUCCEEDED for result in results),
    }


async def _run_gateway_comparison_async() -> list[dict[str, object]]:
    return [
        await _measure_submission_mode("sequential"),
        await _measure_submission_mode("eager_tasks"),
        await _measure_submission_mode("bounded_workers"),
    ]


def gateway_smoke_succeeded(summary: Mapping[str, Any]) -> bool:
    scenarios = summary["scenarios"]
    backpressure = summary["backpressure"]
    comparison = summary["comparison"]
    event_loop = summary["event_loop"]
    slow_upstream = event_loop["slow_upstream"]
    controlled_block = event_loop["controlled_block"]
    return bool(
        scenarios["retry_429_attempts"] == 2
        and scenarios["retry_429_error_history"] == ["rate_limited"]
        and scenarios["retry_500_attempts"] == 2
        and scenarios["retry_500_error_history"] == ["server_error"]
        and scenarios["slow_status"] == GenerationStatus.SUCCEEDED.value
        and scenarios["disconnect_attempts"] == 2
        and scenarios["malformed_status"] == GenerationStatus.FAILED.value
        and scenarios["malformed_error_class"] == "malformed_response"
        and backpressure["batch_succeeded"]
        and backpressure["max_fake_server_active_requests"]
        <= backpressure["configured_concurrency"]
        and all(row["all_succeeded"] for row in comparison)
        and all(row["max_fake_server_active_requests"] <= 4 for row in comparison)
        and slow_upstream["slow_status"] == GenerationStatus.SUCCEEDED.value
        and slow_upstream["fast_status"] == GenerationStatus.SUCCEEDED.value
        and slow_upstream["fast_completed_while_slow_pending"]
        and slow_upstream["heartbeat_count"] >= 3
        and slow_upstream["max_lag_ms"] < slow_upstream["threshold_ms"]
        and controlled_block["block_detected"]
        and controlled_block["max_lag_ms"] >= controlled_block["threshold_ms"]
    )


def gateway_smoke_markdown(summary: Mapping[str, Any]) -> str:
    scenarios = summary["scenarios"]
    backpressure = summary["backpressure"]
    comparison = summary["comparison"]
    event_loop = summary["event_loop"]
    slow_upstream = event_loop["slow_upstream"]
    controlled_block = event_loop["controlled_block"]
    retry_causes = ", ".join(
        scenarios["retry_429_error_history"] + scenarios["retry_500_error_history"]
    )
    return "\n".join(
        [
            "# VeriRun v0.2 Async Gateway Smoke Report",
            "",
            "> This report uses a local fake OpenAI-compatible server. It validates gateway",
            "> control-flow and resource bounds; it is not model performance evidence.",
            "",
            f"- Python: `{summary['environment']['python']}`",
            f"- Platform: `{summary['environment']['platform']}`",
            f"- Source revision: `{summary['source']['revision']}`",
            f"- Working tree clean at start: `{summary['source']['working_tree_clean']}`",
            "",
            "| Scenario | Evidence |",
            "|---|---|",
            f"| 429 retry | {scenarios['retry_429_attempts']} attempts |",
            f"| 5xx retry | {scenarios['retry_500_attempts']} attempts |",
            f"| Retry root causes | {retry_causes} |",
            f"| Slow response | {scenarios['slow_status']} |",
            "| Slow upstream event-loop lag | "
            f"{slow_upstream['max_lag_ms']} ms / {slow_upstream['threshold_ms']} ms |",
            "| Fast request while slow upstream pending | "
            f"{slow_upstream['fast_completed_while_slow_pending']} |",
            "| Controlled block detected | "
            f"{controlled_block['block_detected']} "
            f"({controlled_block['max_lag_ms']} ms / {controlled_block['threshold_ms']} ms) |",
            f"| Disconnect retry | {scenarios['disconnect_attempts']} attempts |",
            f"| Malformed JSON | {scenarios['malformed_error_class']} |",
            "| Bounded fake-server concurrency | "
            f"{backpressure['max_fake_server_active_requests']} / "
            f"{backpressure['configured_concurrency']} |",
            "",
            "## Submission comparison",
            "",
            "The same local 24-request workload (10 ms scripted response) was run three times.",
            "P95 is from admitted HTTP requests; it excludes time waiting in the bounded queue.",
            "",
            "| Submission mode | Duration (ms) | Throughput (req/s) | Admitted P95 (ms) | "
            "Peak traced memory (bytes) |",
            "|---|---:|---:|---:|---:|",
            *[
                "| {mode} | {duration_ms} | {throughput_requests_per_second} | "
                "{admitted_request_p95_ms} | {peak_tracemalloc_bytes} |".format(**row)
                for row in comparison
            ],
            "",
            "## Limitations",
            "",
            "- This deterministic local-fake-server smoke is not a provider compatibility claim.",
            "- The watchdog samples one local event loop. It distinguishes this gateway's",
            "  awaited I/O from a deliberate synchronous block; it is not a host-wide",
            "  scheduling or latency-service-level guarantee.",
            "- The comparison measures Python allocation tracing, not process RSS, and is only",
            "  directional for this machine and local workload.",
            "",
        ]
    )


def run_gateway_smoke(output: Path) -> dict[str, Any]:
    """Run fake-server scenarios and persist canonical, inspectable evidence."""

    summary = asyncio.run(_run_gateway_smoke_async())
    summary["comparison"] = asyncio.run(_run_gateway_comparison_async())
    output.mkdir(parents=True, exist_ok=True)
    write_canonical_json(output / "summary.json", summary)
    (output / "REPORT.md").write_text(gateway_smoke_markdown(summary), encoding="utf-8")
    return summary
