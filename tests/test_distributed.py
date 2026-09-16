from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from verirun.control_plane import (
    AggregationPolicy,
    EvaluationIntent,
    InMemoryControlPlane,
    PlanCompileRequest,
    PlanState,
    RunTaskInput,
    TaskSpec,
    VerifierCatalogEntry,
    compile_verification_plan,
)
from verirun.distributed import (
    BoundedRayExecutor,
    DistributedExecutionError,
    RayExecutionConfig,
    ResourceClass,
    ResourcePool,
    crashing_fixture_operation,
    failing_fixture_operation,
    ray_data_rows,
    ray_data_shards,
    straggler_fixture_operation,
    trusted_fixture_operation,
)
from verirun.reliability import TelemetryRecorder

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _config(max_in_flight: int = 2) -> RayExecutionConfig:
    return RayExecutionConfig(
        max_in_flight=max_in_flight,
        lease_seconds=30,
        resource_pools=(
            ResourcePool(resource_class=ResourceClass.CPU_VERIFIER, ray_resource="verifier_cpu"),
            ResourcePool(resource_class=ResourceClass.GPU_INFERENCE, ray_resource="inference_gpu"),
            ResourcePool(resource_class=ResourceClass.EXTERNAL_API, ray_resource="external_api"),
        ),
    )


def _plane_with_run() -> InMemoryControlPlane:
    plane = InMemoryControlPlane()
    request = PlanCompileRequest(
        task_spec=TaskSpec(task_id="trusted/0", task_family="trusted", verifier_tags=("tests",)),
        evaluation_intent=EvaluationIntent(name="correctness"),
        verifier_catalog=(
            VerifierCatalogEntry(
                verifier_id="trusted-verifier",
                task_families=("trusted",),
                intents=("correctness",),
                adapter="verirun.trusted",
                version="v1",
                config_digest="a" * 64,
                image_digest="b" * 64,
                required_evidence=("stdout",),
                tags=("tests",),
            ),
        ),
        aggregation_policy=AggregationPolicy(
            policy_id="pass-rate", version="v1", reducer="mean", config_digest="c" * 64
        ),
        policy_revision="policy-m4",
        input_evidence_digests=("d" * 64,),
        comparison_cohort_id="cohort-m4",
        candidate_count=3,
        max_concurrency=2,
    )
    plan = compile_verification_plan(plan_id="plan-m4", revision=1, request=request, now=NOW)
    plane.register_plan(plan)
    plane.transition_plan("plan-m4", 1, PlanState.VALIDATED, reason="valid", now=NOW)
    frozen = plane.transition_plan("plan-m4", 1, PlanState.FROZEN, reason="approved", now=NOW)
    plane.create_run(
        idempotency_key="create-m4",
        run_id="run-m4",
        requested_cohort_id="cohort-m4",
        plan_id="plan-m4",
        plan_revision=1,
        plan_digest=frozen.plan_digest,
        tasks=tuple(
            RunTaskInput(
                task_id="trusted/0", candidate_id=f"candidate-{index}", candidate_hash="e" * 64
            )
            for index in range(3)
        ),
        now=NOW,
    )
    return plane


class _FailFirstCommit:
    """Test-only storage outage injected after Ray returned a valid payload."""

    def __init__(self, plane: InMemoryControlPlane) -> None:
        self._plane = plane
        self._failed = False

    def __getattr__(self, name: str):
        return getattr(self._plane, name)

    def commit_result(self, *args, **kwargs):
        if not self._failed:
            self._failed = True
            raise OSError("trusted fixture transient storage outage")
        return self._plane.commit_result(*args, **kwargs)


def test_resource_contract_requires_cpu_and_has_unique_names() -> None:
    with pytest.raises(ValueError, match="cpu_verifier"):
        RayExecutionConfig(
            max_in_flight=1,
            lease_seconds=1,
            resource_pools=(
                ResourcePool(resource_class=ResourceClass.GPU_INFERENCE, ray_resource="gpu"),
            ),
        )
    with pytest.raises(ValueError, match="ray_resource"):
        RayExecutionConfig(
            max_in_flight=1,
            lease_seconds=1,
            resource_pools=(
                ResourcePool(resource_class=ResourceClass.CPU_VERIFIER, ray_resource="shared"),
                ResourcePool(resource_class=ResourceClass.EXTERNAL_API, ray_resource="shared"),
            ),
        )
    with pytest.raises(ValueError, match="custom named"):
        RayExecutionConfig(
            max_in_flight=1,
            lease_seconds=1,
            resource_pools=(
                ResourcePool(resource_class=ResourceClass.CPU_VERIFIER, ray_resource="CPU"),
            ),
        )


def test_ray_data_rows_are_sorted_and_preserve_frozen_plan_lineage() -> None:
    plane = _plane_with_run()
    _, tasks = plane.inspect_run("run-m4")

    rows = ray_data_rows(tasks)

    assert [row["candidate_id"] for row in rows] == ["candidate-0", "candidate-1", "candidate-2"]
    assert {row["verification_plan_id"] for row in rows} == {"plan-m4"}
    assert len({row["verification_plan_digest"] for row in rows}) == 1


def test_ray_data_shards_preserve_manifest_derived_rows() -> None:
    ray = pytest.importorskip("ray")
    ray.init(num_cpus=1, include_dashboard=False, ignore_reinit_error=True)
    try:
        _, tasks = _plane_with_run().inspect_run("run-m4")
        rows = ray_data_shards(tasks, shard_count=2).take_all()
    finally:
        ray.shutdown()

    assert [row["candidate_id"] for row in rows] == ["candidate-0", "candidate-1", "candidate-2"]
    assert {row["verification_plan_id"] for row in rows} == {"plan-m4"}


def test_bounded_ray_executor_commits_driver_verified_results() -> None:
    ray = pytest.importorskip("ray")
    ray.init(
        num_cpus=2,
        resources={"verifier_cpu": 2, "inference_gpu": 1, "external_api": 1},
        include_dashboard=False,
        ignore_reinit_error=True,
    )
    try:
        plane = _plane_with_run()
        telemetry = TelemetryRecorder()
        outcomes = BoundedRayExecutor(_config(), telemetry=telemetry).execute_run(
            plane,
            run_id="run-m4",
            worker_id="driver-m4",
            operation=trusted_fixture_operation,
        )
    finally:
        ray.shutdown()

    assert len(outcomes) == 3
    assert all(outcome.inserted for outcome in outcomes)
    results = plane.list_results("run-m4")
    assert len(results) == 3
    assert len({result.verification_plan_digest for result in results}) == 1
    snapshot = telemetry.snapshot()
    events = snapshot["events"]
    assert isinstance(events, list)
    assert {event["name"] for event in events} >= {
        "verirun.run.execute",
        "verirun.attempt.submit",
        "verirun.attempt.result",
        "verirun.attempt.commit",
    }
    assert {event["comparison_cohort_id"] for event in events if event["task_id"]} == {"cohort-m4"}
    assert snapshot["metrics"]["verirun.attempt.committed"] == 3.0


def test_worker_failure_requires_lease_reclaim_before_retry() -> None:
    ray = pytest.importorskip("ray")
    ray.init(
        num_cpus=1,
        resources={"verifier_cpu": 1, "inference_gpu": 1, "external_api": 1},
        include_dashboard=False,
        ignore_reinit_error=True,
    )
    try:
        plane = _plane_with_run()
        executor = BoundedRayExecutor(_config(max_in_flight=1))
        with pytest.raises(DistributedExecutionError, match="lease remains reclaimable"):
            executor.execute_run(
                plane,
                run_id="run-m4",
                worker_id="driver-m4-crash",
                operation=failing_fixture_operation,
            )
        assert not plane.list_results("run-m4")
        assert plane.reclaim_expired(now=datetime.now(UTC) + timedelta(seconds=31)) == 1

        outcomes = executor.execute_run(
            plane,
            run_id="run-m4",
            worker_id="driver-m4-recovery",
            operation=trusted_fixture_operation,
        )
    finally:
        ray.shutdown()

    assert len(outcomes) == 3
    assert all(outcome.inserted for outcome in outcomes)
    assert len(plane.list_results("run-m4")) == 3


def test_worker_process_crash_requires_lease_reclaim_before_retry() -> None:
    ray = pytest.importorskip("ray")
    ray.init(
        num_cpus=1,
        resources={"verifier_cpu": 1, "inference_gpu": 1, "external_api": 1},
        include_dashboard=False,
        ignore_reinit_error=True,
    )
    try:
        plane = _plane_with_run()
        executor = BoundedRayExecutor(_config(max_in_flight=1))
        with pytest.raises(DistributedExecutionError, match="lease remains reclaimable"):
            executor.execute_run(
                plane,
                run_id="run-m4",
                worker_id="driver-m4-worker-crash",
                operation=crashing_fixture_operation,
            )
        assert not plane.list_results("run-m4")
        assert plane.reclaim_expired(now=datetime.now(UTC) + timedelta(seconds=31)) == 1
        outcomes = executor.execute_run(
            plane,
            run_id="run-m4",
            worker_id="driver-m4-worker-recovery",
            operation=trusted_fixture_operation,
        )
    finally:
        ray.shutdown()

    assert len(outcomes) == 3
    assert all(outcome.inserted for outcome in outcomes)
    assert len(plane.list_results("run-m4")) == 3


def test_actor_process_crash_requires_lease_reclaim_before_retry() -> None:
    ray = pytest.importorskip("ray")
    ray.init(
        num_cpus=1,
        resources={"verifier_cpu": 1, "inference_gpu": 1, "external_api": 1},
        include_dashboard=False,
        ignore_reinit_error=True,
    )
    try:
        plane = _plane_with_run()
        executor = BoundedRayExecutor(_config(max_in_flight=1))
        with pytest.raises(DistributedExecutionError, match="lease remains reclaimable"):
            executor.execute_run_with_actor(
                plane,
                run_id="run-m4",
                worker_id="driver-m4-actor-crash",
                operation=crashing_fixture_operation,
            )
        assert not plane.list_results("run-m4")
        assert plane.reclaim_expired(now=datetime.now(UTC) + timedelta(seconds=31)) == 1
        outcomes = executor.execute_run(
            plane,
            run_id="run-m4",
            worker_id="driver-m4-actor-recovery",
            operation=trusted_fixture_operation,
        )
    finally:
        ray.shutdown()

    assert len(outcomes) == 3
    assert all(outcome.inserted for outcome in outcomes)
    assert len(plane.list_results("run-m4")) == 3


def test_transient_commit_failure_requires_lease_reclaim_before_retry() -> None:
    ray = pytest.importorskip("ray")
    ray.init(
        num_cpus=1,
        resources={"verifier_cpu": 1, "inference_gpu": 1, "external_api": 1},
        include_dashboard=False,
        ignore_reinit_error=True,
    )
    try:
        plane = _plane_with_run()
        executor = BoundedRayExecutor(_config(max_in_flight=1))
        with pytest.raises(DistributedExecutionError, match="durable commit failed"):
            executor.execute_run(
                _FailFirstCommit(plane),
                run_id="run-m4",
                worker_id="driver-m4-storage-outage",
                operation=trusted_fixture_operation,
            )
        assert not plane.list_results("run-m4")
        assert plane.reclaim_expired(now=datetime.now(UTC) + timedelta(seconds=31)) == 1
        outcomes = executor.execute_run(
            plane,
            run_id="run-m4",
            worker_id="driver-m4-storage-recovery",
            operation=trusted_fixture_operation,
        )
    finally:
        ray.shutdown()

    assert len(outcomes) == 3
    assert all(outcome.inserted for outcome in outcomes)
    assert len(plane.list_results("run-m4")) == 3


def test_straggler_preserves_all_commits_and_plan_lineage() -> None:
    ray = pytest.importorskip("ray")
    ray.init(
        num_cpus=2,
        resources={"verifier_cpu": 2, "inference_gpu": 1, "external_api": 1},
        include_dashboard=False,
        ignore_reinit_error=True,
    )
    try:
        plane = _plane_with_run()
        outcomes = BoundedRayExecutor(_config(max_in_flight=2)).execute_run(
            plane,
            run_id="run-m4",
            worker_id="driver-m4-straggler",
            operation=straggler_fixture_operation,
        )
    finally:
        ray.shutdown()

    results = plane.list_results("run-m4")
    assert len(outcomes) == 3
    assert all(outcome.inserted for outcome in outcomes)
    assert {result.result_payload["status"] for result in results} == {"passed"}
    assert {result.result_payload["delay_seconds"] for result in results} == {0.0, 0.2}
    assert len({result.verification_plan_digest for result in results}) == 1
