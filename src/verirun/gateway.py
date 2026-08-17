"""Bounded async OpenAI-compatible candidate generation gateway."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterable, Mapping
from enum import StrEnum
from time import perf_counter
from typing import Any, Literal

import httpx
from pydantic import Field, model_validator

from verirun.models import FrozenModel, GenerationSpec, ModelSpec, NonEmpty


class GenerationStatus(StrEnum):
    """Terminal state of one model-generation request."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GatewayErrorClass(StrEnum):
    """Stable, root-cause-preserving model gateway failure classes."""

    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    MALFORMED_RESPONSE = "malformed_response"
    CLIENT_ERROR = "client_error"


class GatewayConfig(FrozenModel):
    """Non-secret resource and retry policy for one model endpoint."""

    base_url: NonEmpty
    max_concurrency: int = Field(default=4, gt=0)
    requests_per_second: float = Field(default=4.0, gt=0)
    max_in_flight_tokens: int = Field(default=4_096, gt=0)
    queue_capacity: int = Field(default=16, gt=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    read_timeout_seconds: float = Field(default=30.0, gt=0)
    write_timeout_seconds: float = Field(default=10.0, gt=0)
    pool_timeout_seconds: float = Field(default=5.0, gt=0)
    max_attempts: int = Field(default=3, gt=0)
    retry_base_delay_seconds: float = Field(default=0.25, ge=0)
    retry_max_delay_seconds: float = Field(default=5.0, ge=0)

    @model_validator(mode="after")
    def validate_retry_delay(self) -> GatewayConfig:
        if self.retry_max_delay_seconds < self.retry_base_delay_seconds:
            raise ValueError("retry_max_delay_seconds cannot be less than retry_base_delay_seconds")
        return self


class GenerationRequest(FrozenModel):
    """One candidate-generation request; prompts are never placed in reports."""

    request_id: NonEmpty
    task_id: NonEmpty
    prompt: str
    model: ModelSpec
    generation: GenerationSpec = GenerationSpec()


class GenerationResult(FrozenModel):
    """Structured outcome of a model call, independent from later verification."""

    schema_version: Literal["verirun.generation-result/v1"] = "verirun.generation-result/v1"
    request_id: NonEmpty
    task_id: NonEmpty
    status: GenerationStatus
    completion_texts: tuple[str, ...] = ()
    attempts: int = Field(ge=1)
    latency_ms: int = Field(ge=0)
    error_class: GatewayErrorClass | None = None
    error_message: str | None = None
    attempt_error_classes: tuple[GatewayErrorClass, ...] = ()

    @model_validator(mode="after")
    def validate_terminal_state(self) -> GenerationResult:
        if self.status is GenerationStatus.SUCCEEDED:
            has_error = self.error_class is not None or self.error_message is not None
            if not self.completion_texts or has_error:
                raise ValueError("successful generation results require completions and no error")
        elif self.error_class is None or self.error_message is None:
            raise ValueError("failed generation results require an error class and message")
        return self


class GatewayBatchError(RuntimeError):
    """A fail-fast batch stopped after a classified request failure."""

    def __init__(
        self,
        failure: GenerationResult,
        partial_results: tuple[GenerationResult, ...],
    ) -> None:
        self.failure = failure
        self.partial_results = partial_results
        super().__init__(f"fail-fast batch stopped on {failure.request_id}: {failure.error_class}")


class _RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self._interval = 1 / requests_per_second
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            granted_at = max(now, self._next_allowed_at)
            self._next_allowed_at = granted_at + self._interval
        delay = granted_at - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)


class _TokenBudget:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._in_use = 0
        self._condition = asyncio.Condition()

    async def acquire(self, tokens: int) -> None:
        if tokens > self._capacity:
            raise ValueError("request max_tokens exceeds max_in_flight_tokens")
        async with self._condition:
            await self._condition.wait_for(lambda: self._in_use + tokens <= self._capacity)
            self._in_use += tokens

    async def release(self, tokens: int) -> None:
        async with self._condition:
            self._in_use -= tokens
            self._condition.notify_all()


class AsyncModelGateway:
    """A reusable OpenAI-compatible client with bounded admission and retries."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._api_key = api_key
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._concurrency = asyncio.Semaphore(config.max_concurrency)
        self._rate_limiter = _RateLimiter(config.requests_per_second)
        self._token_budget = _TokenBudget(config.max_in_flight_tokens)
        self._random = random.Random(0)

    async def __aenter__(self) -> AsyncModelGateway:
        await self._get_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close pooled idle and in-flight HTTP connections once work has stopped."""

        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.aclose()

    async def generate_one(self, request: GenerationRequest) -> GenerationResult:
        """Generate one request while preserving cancellation for the parent caller."""

        started_at = perf_counter()
        tokens = request.generation.max_tokens
        await self._token_budget.acquire(tokens)
        try:
            async with self._concurrency:
                await self._rate_limiter.acquire()
                result = await self._request_with_retries(request, started_at)
        finally:
            await self._token_budget.release(tokens)
        return result

    async def generate_many(
        self,
        requests: Iterable[GenerationRequest],
        *,
        fail_fast: bool = False,
    ) -> tuple[GenerationResult, ...]:
        """Run a batch with bounded workers and optionally stop at the first failure."""

        ordered_requests = tuple(requests)
        request_ids = [request.request_id for request in ordered_requests]
        if len(set(request_ids)) != len(request_ids):
            raise ValueError("batch request_id values must be unique")

        queue: asyncio.Queue[GenerationRequest | None] = asyncio.Queue(
            maxsize=self._config.queue_capacity
        )
        results: dict[str, GenerationResult] = {}
        worker_count = min(self._config.max_concurrency, len(ordered_requests))
        if worker_count == 0:
            return ()

        async def producer() -> None:
            for request in ordered_requests:
                await queue.put(request)
            for _ in range(worker_count):
                await queue.put(None)

        def partial_results() -> tuple[GenerationResult, ...]:
            return tuple(results[request_id] for request_id in request_ids if request_id in results)

        async def worker() -> None:
            while True:
                request = await queue.get()
                try:
                    if request is None:
                        return
                    result = await self.generate_one(request)
                    results[request.request_id] = result
                    if fail_fast and result.status is GenerationStatus.FAILED:
                        raise GatewayBatchError(result, partial_results())
                finally:
                    queue.task_done()

        tasks = [asyncio.create_task(producer(), name="verirun-gateway-producer")]
        tasks.extend(
            asyncio.create_task(worker(), name=f"verirun-gateway-worker-{index}")
            for index in range(worker_count)
        )
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return tuple(results[request_id] for request_id in request_ids)

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._client is None:
                headers: dict[str, str] = {"accept": "application/json"}
                if self._api_key is not None:
                    headers["authorization"] = f"Bearer {self._api_key}"
                self._client = httpx.AsyncClient(
                    base_url=self._config.base_url.rstrip("/") + "/",
                    headers=headers,
                    timeout=httpx.Timeout(
                        connect=self._config.connect_timeout_seconds,
                        read=self._config.read_timeout_seconds,
                        write=self._config.write_timeout_seconds,
                        pool=self._config.pool_timeout_seconds,
                    ),
                    limits=httpx.Limits(
                        max_connections=self._config.max_concurrency,
                        max_keepalive_connections=self._config.max_concurrency,
                    ),
                    transport=self._transport,
                )
            return self._client

    async def _request_with_retries(
        self,
        request: GenerationRequest,
        started_at: float,
    ) -> GenerationResult:
        client = await self._get_client()
        attempt_error_classes: list[GatewayErrorClass] = []
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                response = await client.post("v1/chat/completions", json=_openai_payload(request))
            except httpx.TimeoutException:
                error_class = GatewayErrorClass.TIMEOUT
                message = "HTTP timeout"
            except httpx.TransportError:
                error_class = GatewayErrorClass.TRANSPORT
                message = "HTTP transport error"
            else:
                if response.status_code == 429:
                    error_class = GatewayErrorClass.RATE_LIMITED
                    message = "HTTP status 429"
                elif response.status_code >= 500:
                    error_class = GatewayErrorClass.SERVER_ERROR
                    message = f"HTTP status {response.status_code}"
                elif response.status_code >= 400:
                    attempt_error_classes.append(GatewayErrorClass.CLIENT_ERROR)
                    return _failed_result(
                        request,
                        attempt,
                        started_at,
                        GatewayErrorClass.CLIENT_ERROR,
                        f"HTTP status {response.status_code}",
                        attempt_error_classes=tuple(attempt_error_classes),
                    )
                else:
                    try:
                        payload = response.json()
                    except ValueError:
                        attempt_error_classes.append(GatewayErrorClass.MALFORMED_RESPONSE)
                        return _failed_result(
                            request,
                            attempt,
                            started_at,
                            GatewayErrorClass.MALFORMED_RESPONSE,
                            "response body is not valid JSON",
                            attempt_error_classes=tuple(attempt_error_classes),
                        )
                    parsed = _parse_openai_response(payload)
                    if isinstance(parsed, tuple):
                        return GenerationResult(
                            request_id=request.request_id,
                            task_id=request.task_id,
                            status=GenerationStatus.SUCCEEDED,
                            completion_texts=parsed,
                            attempts=attempt,
                            latency_ms=_elapsed_ms(started_at),
                            attempt_error_classes=tuple(attempt_error_classes),
                        )
                    return _failed_result(
                        request,
                        attempt,
                        started_at,
                        GatewayErrorClass.MALFORMED_RESPONSE,
                        parsed,
                        attempt_error_classes=(
                            *attempt_error_classes,
                            GatewayErrorClass.MALFORMED_RESPONSE,
                        ),
                    )

            attempt_error_classes.append(error_class)
            if attempt == self._config.max_attempts:
                return _failed_result(
                    request,
                    attempt,
                    started_at,
                    error_class,
                    message,
                    attempt_error_classes=tuple(attempt_error_classes),
                )
            await asyncio.sleep(self._retry_delay(attempt))

        raise AssertionError("retry loop must return a result")

    def _retry_delay(self, attempt: int) -> float:
        ceiling = min(
            self._config.retry_max_delay_seconds,
            self._config.retry_base_delay_seconds * (2 ** (attempt - 1)),
        )
        return self._random.uniform(0, ceiling)


def _openai_payload(request: GenerationRequest) -> dict[str, object]:
    generation = request.generation
    return {
        "model": request.model.model_revision,
        "messages": [{"role": "user", "content": request.prompt}],
        "temperature": generation.temperature,
        "top_p": generation.top_p,
        "max_tokens": generation.max_tokens,
        "n": generation.n,
        "seed": generation.seed,
    }


def _parse_openai_response(payload: Any) -> tuple[str, ...] | str:
    if not isinstance(payload, Mapping):
        return "response body must be a JSON object"
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return "response body must contain non-empty choices"
    completions: list[str] = []
    for choice in choices:
        if not isinstance(choice, Mapping):
            return "response choices must be objects"
        message = choice.get("message")
        if not isinstance(message, Mapping):
            return "response choices must contain message objects"
        content = message.get("content")
        if not isinstance(content, str) or not content:
            return "response message content must be a non-empty string"
        completions.append(content)
    return tuple(completions)


def _elapsed_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1_000)


def _failed_result(
    request: GenerationRequest,
    attempts: int,
    started_at: float,
    error_class: GatewayErrorClass,
    error_message: str,
    *,
    attempt_error_classes: tuple[GatewayErrorClass, ...] = (),
) -> GenerationResult:
    return GenerationResult(
        request_id=request.request_id,
        task_id=request.task_id,
        status=GenerationStatus.FAILED,
        attempts=attempts,
        latency_ms=_elapsed_ms(started_at),
        error_class=error_class,
        error_message=error_message,
        attempt_error_classes=attempt_error_classes,
    )
