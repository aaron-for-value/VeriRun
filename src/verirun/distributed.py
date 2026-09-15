"""Bounded Ray execution that preserves v0.4 control-plane authority.

Ray workers only execute already-claimed attempts.  The driver remains the sole
writer of durable lease and final-result state, so a framework retry cannot become a
business-idempotency mechanism.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Mapping
from enum import StrEnum
from importlib import import_module
from typing import Annotated, Any, Protocol

from pydantic import Field, model_validator

from verirun.canonical import content_hash
from verirun.control_plane import (
    AttemptLease,
    CommitOutcome,
    EvalRunRecord,
    FailureDomain,
    RunState,
    RunTaskRecord,
)
from verirun.models import FrozenModel, NonEmpty, Sha256


class DistributedExecutionError(RuntimeError):
    """Raised when the execution plane cannot complete an already-claimed attempt."""


class ResourceClass(StrEnum):
    """Capacity classes accepted by the M4 scheduler."""

    CPU_VERIFIER = "cpu_verifier"
    GPU_INFERENCE = "gpu_inference"
    EXTERNAL_API = "external_api"


class ResourcePool(FrozenModel):
    """A named Ray resource; it is an admission contract, not hardware evidence."""

    resource_class: ResourceClass
    ray_resource: NonEmpty
    units_per_task: Annotated[float, Field(gt=0)] = 1.0


class RayExecutionConfig(FrozenModel):
    """Static bounded-submission policy for a distributed execution run."""

    max_in_flight: Annotated[int, Field(gt=0)]
    lease_seconds: Annotated[float, Field(gt=0)]
    resource_pools: tuple[ResourcePool, ...]

    @model_validator(mode="after")
    def validate_pools(self) -> RayExecutionConfig:
        classes = [pool.resource_class for pool in self.resource_pools]
        names = [pool.ray_resource for pool in self.resource_pools]
        if len(classes) != len(set(classes)):
            raise ValueError("resource_class values must be unique")
        if len(names) != len(set(names)):
            raise ValueError("ray_resource values must be unique")
        builtin_resources = {"CPU", "GPU", "memory"}
        if any(name in builtin_resources for name in names):
            raise ValueError("M4 resource pools must use custom named Ray resources")
        if ResourceClass.CPU_VERIFIER not in classes:
            raise ValueError("cpu_verifier resource pool is required")
        return self

    def pool_for(self, resource_class: ResourceClass) -> ResourcePool:
        for pool in self.resource_pools:
            if pool.resource_class is resource_class:
                return pool
        raise ValueError(f"resource class {resource_class.value!r} is not configured")


class RayWorkItem(FrozenModel):
    """Immutable claim lineage delivered to one Ray task."""

    run_id: NonEmpty
    task_id: NonEmpty
    candidate_id: NonEmpty
    candidate_hash: Sha256
    attempt_id: NonEmpty
    lease_token: NonEmpty
    verification_plan_id: NonEmpty
    verification_plan_digest: Sha256
    resource_class: ResourceClass


class RayWorkResult(FrozenModel):
    """Envelope returned by a Ray worker before the driver commits it durably."""

    attempt_id: NonEmpty
    verification_plan_digest: Sha256
    result_payload: dict[str, object]
    failure_domain: FailureDomain | None = None


class ControlPlaneExecutionBackend(Protocol):
    """The narrow M3 API required by the M4 driver."""

    def claim_task(
        self,
        *,
        run_id: str,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> AttemptLease | None: ...

    def commit_result(
        self,
        attempt_id: str,
        *,
        worker_id: str,
        lease_token: str,
        result_digest: str,
        result_payload: dict[str, object],
        failure_domain: FailureDomain | None = None,
    ) -> CommitOutcome: ...

    def inspect_run(self, run_id: str) -> tuple[EvalRunRecord, tuple[RunTaskRecord, ...]]: ...


WorkOperation = Callable[[RayWorkItem], Mapping[str, object]]


def ray_data_rows(tasks: tuple[RunTaskRecord, ...]) -> tuple[dict[str, str], ...]:
    """Create deterministic, manifest-derived Ray Data ingestion rows.

    This intentionally excludes candidate bytes and verifier selection inputs: both
    remain fixed by existing candidate artifacts and the frozen verification plan.
    """

    return tuple(
        {
            "task_id": task.task_id,
            "candidate_id": task.candidate_id,
            "candidate_hash": task.candidate_hash,
            "verification_plan_id": task.verification_plan_id,
            "verification_plan_digest": task.verification_plan_digest,
        }
        for task in sorted(tasks, key=lambda item: (item.task_id, item.candidate_id))
    )


def ray_data_shards(tasks: tuple[RunTaskRecord, ...], *, shard_count: int) -> Any:
    """Build a versioned Ray Data dataset with a bounded number of shards.

    Importing Ray is delayed so the core protocol remains installable without the
    optional distributed-executor extra.
    """

    if shard_count <= 0:
        raise ValueError("shard_count must be positive")
    try:
        ray_data = import_module("ray.data")
    except ModuleNotFoundError as exc:
        raise DistributedExecutionError(
            "install verirun[distributed-executor] before using Ray Data"
        ) from exc
    return ray_data.from_items(list(ray_data_rows(tasks))).repartition(shard_count, shuffle=False)


class BoundedRayExecutor:
    """Submit a finite amount of work with ``ray.wait`` and driver-side commit."""

    def __init__(self, config: RayExecutionConfig) -> None:
        self._config = config

    def execute_run(
        self,
        plane: ControlPlaneExecutionBackend,
        *,
        run_id: str,
        worker_id: str,
        operation: WorkOperation,
        resource_class: ResourceClass = ResourceClass.CPU_VERIFIER,
    ) -> tuple[CommitOutcome, ...]:
        """Claim, execute, and commit all currently queueable tasks for one run.

        No worker receives the control-plane client.  A Ray failure leaves the lease
        active for the M3 reclaim path instead of forging an unverified final result.
        """

        ray = _require_ray()
        if not ray.is_initialized():
            raise DistributedExecutionError("Ray must be initialized by the caller")

        pool = self._config.pool_for(resource_class)
        remote_operation = ray.remote(_execute_claimed_work).options(
            num_cpus=1,
            resources={pool.ray_resource: pool.units_per_task},
            max_retries=0,
        )
        return self._execute_claims(
            plane,
            run_id=run_id,
            worker_id=worker_id,
            resource_class=resource_class,
            submit_work=lambda item: remote_operation.remote(item, operation),
        )

    def execute_run_with_actor(
        self,
        plane: ControlPlaneExecutionBackend,
        *,
        run_id: str,
        worker_id: str,
        operation: WorkOperation,
        resource_class: ResourceClass = ResourceClass.CPU_VERIFIER,
    ) -> tuple[CommitOutcome, ...]:
        """Execute claimed work through one non-restarting Ray actor.

        The actor is deliberately ephemeral: a crash must surface to the driver and
        leave its durable lease reclaimable rather than becoming an implicit actor
        retry. This path is for stateful verifier implementations that can safely
        rebuild their ephemeral state from the immutable work item.
        """

        ray = _require_ray()
        if not ray.is_initialized():
            raise DistributedExecutionError("Ray must be initialized by the caller")

        pool = self._config.pool_for(resource_class)
        actor = (
            ray.remote(_ClaimedWorkActor)
            .options(
                num_cpus=1,
                resources={pool.ray_resource: pool.units_per_task},
                max_restarts=0,
                max_task_retries=0,
            )
            .remote()
        )
        return self._execute_claims(
            plane,
            run_id=run_id,
            worker_id=worker_id,
            resource_class=resource_class,
            submit_work=lambda item: actor.execute.remote(item, operation),
        )

    def _execute_claims(
        self,
        plane: ControlPlaneExecutionBackend,
        *,
        run_id: str,
        worker_id: str,
        resource_class: ResourceClass,
        submit_work: Callable[[RayWorkItem], object],
    ) -> tuple[CommitOutcome, ...]:
        """Drive bounded claims while keeping all durable mutations local."""

        ray = _require_ray()
        in_flight: dict[object, RayWorkItem] = {}
        outcomes: list[CommitOutcome] = []

        while True:
            run, _ = plane.inspect_run(run_id)
            if run.state is RunState.COMPLETED:
                return tuple(outcomes)
            while len(in_flight) < self._config.max_in_flight:
                lease = plane.claim_task(
                    run_id=run_id,
                    worker_id=worker_id,
                    attempt_id=uuid.uuid4().hex,
                    lease_token=uuid.uuid4().hex,
                    lease_seconds=self._config.lease_seconds,
                )
                if lease is None:
                    break
                item = RayWorkItem(
                    run_id=lease.run_id,
                    task_id=lease.task_id,
                    candidate_id=lease.candidate_id,
                    candidate_hash=_candidate_hash(plane, lease),
                    attempt_id=lease.attempt_id,
                    lease_token=lease.lease_token,
                    verification_plan_id=lease.verification_plan_id,
                    verification_plan_digest=lease.verification_plan_digest,
                    resource_class=resource_class,
                )
                in_flight[submit_work(item)] = item

            if not in_flight:
                return tuple(outcomes)

            ready, _ = ray.wait(list(in_flight), num_returns=1)
            reference = ready[0]
            item = in_flight.pop(reference)
            try:
                result = ray.get(reference)
            except Exception as exc:  # Ray reports task/worker failures as framework errors.
                raise DistributedExecutionError(
                    f"Ray failed attempt {item.attempt_id}; lease remains reclaimable"
                ) from exc
            if result.attempt_id != item.attempt_id:
                raise DistributedExecutionError(
                    "Ray result attempt lineage does not match its claim"
                )
            if result.verification_plan_digest != item.verification_plan_digest:
                raise DistributedExecutionError("Ray result changed the frozen verification plan")
            try:
                outcome = plane.commit_result(
                    item.attempt_id,
                    worker_id=worker_id,
                    lease_token=item.lease_token,
                    result_digest=content_hash(result.result_payload),
                    result_payload=result.result_payload,
                    failure_domain=result.failure_domain,
                )
            except Exception as exc:
                raise DistributedExecutionError(
                    "durable commit failed for attempt "
                    f"{item.attempt_id}; lease remains reclaimable"
                ) from exc
            outcomes.append(outcome)


def _candidate_hash(plane: ControlPlaneExecutionBackend, lease: AttemptLease) -> str:
    _, tasks = plane.inspect_run(lease.run_id)
    for task in tasks:
        if task.task_id == lease.task_id and task.candidate_id == lease.candidate_id:
            return task.candidate_hash
    raise DistributedExecutionError("claimed task is missing from its durable run")


def _execute_claimed_work(item: RayWorkItem, operation: WorkOperation) -> RayWorkResult:
    """Ray-side work function: execute payload generation only, never commit."""

    return RayWorkResult(
        attempt_id=item.attempt_id,
        verification_plan_digest=item.verification_plan_digest,
        result_payload=dict(operation(item)),
    )


class _ClaimedWorkActor:
    """Ephemeral Ray actor that computes one immutable work item at a time."""

    def execute(self, item: RayWorkItem, operation: WorkOperation) -> RayWorkResult:
        return _execute_claimed_work(item, operation)


def trusted_fixture_operation(_: RayWorkItem) -> Mapping[str, object]:
    """A package-owned CPU fixture operation used by the M4 reference environment."""

    return {"status": "passed", "source": "trusted-fixture"}


def failing_fixture_operation(_: RayWorkItem) -> Mapping[str, object]:
    """A deterministic worker-crash probe for M4 recovery evidence."""

    raise RuntimeError("trusted fixture deliberately failed before durable commit")


def crashing_fixture_operation(_: RayWorkItem) -> Mapping[str, object]:
    """Terminate the executing Ray process before it can return a result."""

    os._exit(17)


def straggler_fixture_operation(item: RayWorkItem) -> Mapping[str, object]:
    """Delay one stable fixture candidate without changing its frozen lineage."""

    delay_seconds = 0.2 if item.candidate_id == "candidate-0" else 0.0
    time.sleep(delay_seconds)
    return {
        "status": "passed",
        "source": "trusted-fixture-straggler",
        "delay_seconds": delay_seconds,
    }


def timed_fixture_operation(_: RayWorkItem) -> Mapping[str, object]:
    """Small fixed-latency CPU trusted fixture for logical concurrency evidence."""

    started_at = time.perf_counter()
    time.sleep(0.02)
    return {
        "status": "passed",
        "source": "trusted-fixture-concurrency",
        "worker_duration_ms": int((time.perf_counter() - started_at) * 1000),
    }


def _require_ray() -> Any:
    try:
        return import_module("ray")
    except ModuleNotFoundError as exc:
        raise DistributedExecutionError(
            "install verirun[distributed-executor] before using the Ray executor"
        ) from exc
