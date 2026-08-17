from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from verirun.gateway import (
    AsyncModelGateway,
    GatewayBatchError,
    GatewayConfig,
    GatewayErrorClass,
    GenerationRequest,
    GenerationStatus,
)
from verirun.gateway_smoke import (
    ScriptedOpenAIServer,
    ScriptedResponse,
    gateway_smoke_succeeded,
    run_gateway_smoke,
)
from verirun.models import GenerationSpec, ModelSpec


def request(name: str, *, max_tokens: int = 4) -> GenerationRequest:
    return GenerationRequest(
        request_id=f"request-{name}",
        task_id=f"task-{name}",
        prompt=name,
        model=ModelSpec(endpoint_type="openai-compatible", model_revision="fake-model-v1"),
        generation=GenerationSpec(max_tokens=max_tokens),
    )


def config(server: ScriptedOpenAIServer, **overrides: object) -> GatewayConfig:
    values: dict[str, object] = {
        "base_url": server.base_url,
        "max_concurrency": 2,
        "requests_per_second": 1_000,
        "max_in_flight_tokens": 8,
        "queue_capacity": 2,
        "read_timeout_seconds": 0.5,
        "max_attempts": 2,
        "retry_base_delay_seconds": 0,
        "retry_max_delay_seconds": 0,
    }
    values.update(overrides)
    return GatewayConfig.model_validate(values)


def test_fake_server_classifies_and_retries_faults() -> None:
    async def scenario() -> None:
        plans = {
            "rate": [ScriptedResponse(status_code=429), ScriptedResponse()],
            "server": [ScriptedResponse(status_code=500), ScriptedResponse()],
            "slow": [ScriptedResponse(delay_seconds=0.01)],
            "disconnect": [ScriptedResponse(disconnect=True), ScriptedResponse()],
            "malformed": [ScriptedResponse(raw_body="{")],
        }
        async with (
            ScriptedOpenAIServer(plans) as server,
            AsyncModelGateway(config(server)) as gateway,
        ):
            results = await gateway.generate_many(
                [request(name) for name in plans], fail_fast=False
            )

        by_id = {result.request_id: result for result in results}
        assert by_id["request-rate"].attempts == 2
        assert by_id["request-rate"].attempt_error_classes == (GatewayErrorClass.RATE_LIMITED,)
        assert by_id["request-server"].attempts == 2
        assert by_id["request-server"].attempt_error_classes == (GatewayErrorClass.SERVER_ERROR,)
        assert by_id["request-slow"].status is GenerationStatus.SUCCEEDED
        assert by_id["request-disconnect"].attempts == 2
        assert by_id["request-malformed"].error_class is GatewayErrorClass.MALFORMED_RESPONSE

    asyncio.run(scenario())


def test_partial_failure_preserves_successful_results() -> None:
    async def scenario() -> None:
        async with (
            ScriptedOpenAIServer(
                {"bad": [ScriptedResponse(status_code=400)], "good": [ScriptedResponse()]}
            ) as server,
            AsyncModelGateway(config(server)) as gateway,
        ):
            results = await gateway.generate_many([request("bad"), request("good")])
        assert [result.status for result in results] == [
            GenerationStatus.FAILED,
            GenerationStatus.SUCCEEDED,
        ]
        assert results[0].error_class is GatewayErrorClass.CLIENT_ERROR

    asyncio.run(scenario())


def test_fail_fast_cancels_workers_and_gateway_remains_usable() -> None:
    async def scenario() -> None:
        plans = {
            "bad": [ScriptedResponse(status_code=500)],
            "slow": [ScriptedResponse(delay_seconds=0.2)],
            "after": [ScriptedResponse()],
        }
        async with (
            ScriptedOpenAIServer(plans) as server,
            AsyncModelGateway(config(server, max_attempts=1)) as gateway,
        ):
            with pytest.raises(GatewayBatchError) as caught:
                await gateway.generate_many([request("bad"), request("slow")], fail_fast=True)
            after = await gateway.generate_one(request("after"))
        assert caught.value.failure.error_class is GatewayErrorClass.SERVER_ERROR
        assert after.status is GenerationStatus.SUCCEEDED

    asyncio.run(scenario())


def test_parent_cancellation_cleans_up_and_allows_follow_up_work() -> None:
    async def scenario() -> None:
        plans = {
            "slow": [ScriptedResponse(delay_seconds=0.05)],
            "after": [ScriptedResponse()],
        }
        async with (
            ScriptedOpenAIServer(plans) as server,
            AsyncModelGateway(config(server)) as gateway,
        ):
            batch = asyncio.create_task(gateway.generate_many([request("slow")]))
            while server.active_requests == 0:
                await asyncio.sleep(0.001)
            batch.cancel()
            with pytest.raises(asyncio.CancelledError):
                await batch
            await asyncio.sleep(0.06)
            assert server.active_requests == 0
            after = await gateway.generate_one(request("after"))
        assert after.status is GenerationStatus.SUCCEEDED

    asyncio.run(scenario())


def test_timeout_cleanup_returns_classified_failure_and_reuses_client() -> None:
    async def scenario() -> None:
        plans = {
            "slow": [ScriptedResponse(delay_seconds=0.04)],
            "after": [ScriptedResponse()],
        }
        async with (
            ScriptedOpenAIServer(plans) as server,
            AsyncModelGateway(
                config(server, read_timeout_seconds=0.005, max_attempts=1)
            ) as gateway,
        ):
            timed_out = await gateway.generate_one(request("slow"))
            await asyncio.sleep(0.05)
            after = await gateway.generate_one(request("after"))
        assert timed_out.error_class is GatewayErrorClass.TIMEOUT
        assert after.status is GenerationStatus.SUCCEEDED

    asyncio.run(scenario())


def test_backpressure_enforces_concurrency_and_token_budget() -> None:
    async def scenario() -> None:
        plans = {name: [ScriptedResponse(delay_seconds=0.02)] for name in ("one", "two", "three")}
        async with ScriptedOpenAIServer(plans) as server:
            gateway_config = config(
                server,
                max_concurrency=2,
                max_in_flight_tokens=4,
                queue_capacity=1,
            )
            async with AsyncModelGateway(gateway_config) as gateway:
                results = await gateway.generate_many(
                    [request(name, max_tokens=4) for name in plans]
                )
        assert all(result.status is GenerationStatus.SUCCEEDED for result in results)
        assert server.max_active_requests == 1

    asyncio.run(scenario())


def test_qps_admission_spaces_request_starts() -> None:
    async def scenario() -> None:
        plans = {name: [ScriptedResponse()] for name in ("one", "two")}
        async with ScriptedOpenAIServer(plans) as server:
            async with AsyncModelGateway(config(server, requests_per_second=20)) as gateway:
                await gateway.generate_many([request("one"), request("two")])
            starts = [server.request_started_at[name][0] for name in ("one", "two")]
        assert starts[1] - starts[0] >= 0.035

    asyncio.run(scenario())


def test_rejects_requests_larger_than_token_budget() -> None:
    async def scenario() -> None:
        async with (
            ScriptedOpenAIServer({"large": [ScriptedResponse()]}) as server,
            AsyncModelGateway(config(server, max_in_flight_tokens=3)) as gateway,
        ):
            with pytest.raises(ValueError, match="max_tokens"):
                await gateway.generate_one(request("large", max_tokens=4))

    asyncio.run(scenario())


def test_gateway_smoke_writes_fault_and_submission_evidence(tmp_path: Path) -> None:
    summary = run_gateway_smoke(tmp_path / "gateway")

    assert gateway_smoke_succeeded(summary)
    assert (tmp_path / "gateway" / "summary.json").is_file()
    assert (tmp_path / "gateway" / "REPORT.md").is_file()
