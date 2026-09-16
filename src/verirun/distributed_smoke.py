"""Trusted-fixture local Ray replay evidence for the M4 execution contract."""

from __future__ import annotations

import gc
import os
import platform
import resource
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast
from urllib.error import URLError
from urllib.request import urlopen

from verirun.canonical import write_canonical_json
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
    ControlPlaneExecutionBackend,
    DistributedExecutionError,
    RayExecutionConfig,
    ResourceClass,
    ResourcePool,
    crashing_fixture_operation,
    ray_data_shards,
    straggler_fixture_operation,
    timed_fixture_operation,
    trusted_fixture_operation,
)
from verirun.provenance import source_state
from verirun.reliability import TelemetryRecorder


def _new_plane(
    *, run_id: str = "m4-trusted-fixtures", candidate_count: int = 3
) -> tuple[InMemoryControlPlane, str]:
    """Create the fixed, frozen M4 CPU-fixture run used for both passes."""

    plane = InMemoryControlPlane()
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
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
        policy_revision="m4-trusted-fixtures-v1",
        input_evidence_digests=("d" * 64,),
        comparison_cohort_id="m4-trusted-fixtures",
        candidate_count=candidate_count,
        max_concurrency=2,
    )
    plan = compile_verification_plan(plan_id=run_id, revision=1, request=request, now=now)
    plane.register_plan(plan)
    plane.transition_plan(plan.plan_id, 1, PlanState.VALIDATED, reason="fixture", now=now)
    frozen = plane.transition_plan(plan.plan_id, 1, PlanState.FROZEN, reason="fixture", now=now)
    plane.create_run(
        idempotency_key=f"create-{run_id}",
        run_id=run_id,
        requested_cohort_id="m4-trusted-fixtures",
        plan_id=plan.plan_id,
        plan_revision=1,
        plan_digest=frozen.plan_digest,
        tasks=tuple(
            RunTaskInput(
                task_id="trusted/0", candidate_id=f"candidate-{index}", candidate_hash="e" * 64
            )
            for index in range(candidate_count)
        ),
        now=now,
    )
    return plane, frozen.plan_digest


def _ray() -> Any:
    try:
        return import_module("ray")
    except ModuleNotFoundError as exc:
        raise RuntimeError("install verirun[distributed-executor] before running M4 smoke") from exc


def _initialize_ray(ray: Any) -> None:
    """Join a RayJob cluster when supplied, otherwise start a local fixture cluster."""

    address = os.environ.get("RAY_ADDRESS")
    if address:
        ray.init(address=address, ignore_reinit_error=True)
        return
    ray.init(
        num_cpus=2,
        resources={"verifier_cpu": 2},
        include_dashboard=False,
        ignore_reinit_error=True,
    )


def _run_once(*, worker_id: str, telemetry: TelemetryRecorder | None = None) -> dict[str, object]:
    ray = _ray()
    _initialize_ray(ray)
    try:
        plane, plan_digest = _new_plane()
        _, tasks = plane.inspect_run("m4-trusted-fixtures")
        rows = ray_data_shards(tasks, shard_count=2).take_all()
        outcomes = BoundedRayExecutor(
            RayExecutionConfig(
                max_in_flight=2,
                lease_seconds=30,
                resource_pools=(
                    ResourcePool(
                        resource_class=ResourceClass.CPU_VERIFIER,
                        ray_resource="verifier_cpu",
                    ),
                ),
            ),
            telemetry=telemetry,
        ).execute_run(
            plane,
            run_id="m4-trusted-fixtures",
            worker_id=worker_id,
            operation=trusted_fixture_operation,
        )
        run, _ = plane.inspect_run("m4-trusted-fixtures")
        results = plane.list_results("m4-trusted-fixtures")
    finally:
        ray.shutdown()

    return {
        "plan_digest": plan_digest,
        "run_state": run.state.value,
        "ray_data_candidate_ids": [str(row["candidate_id"]) for row in rows],
        "result_statuses": [str(result.result_payload["status"]) for result in results],
        "result_plan_digests": [result.verification_plan_digest for result in results],
        "all_commits_inserted": all(outcome.inserted for outcome in outcomes),
        "telemetry": telemetry.snapshot() if telemetry is not None else None,
    }


def run_distributed_smoke(output: Path) -> dict[str, object]:
    """Run the frozen CPU fixture twice and persist its semantic comparison."""

    baseline = _run_once(worker_id="m4-local-baseline")
    replay = _run_once(worker_id="m4-local-replay")
    checks = {
        "baseline_completed": baseline["run_state"] == "completed",
        "replay_completed": replay["run_state"] == "completed",
        "ray_data_rows_match": baseline["ray_data_candidate_ids"]
        == replay["ray_data_candidate_ids"]
        == ["candidate-0", "candidate-1", "candidate-2"],
        "plan_digest_matches": baseline["plan_digest"] == replay["plan_digest"]
        and len(set(cast(list[str], baseline["result_plan_digests"]))) == 1
        and len(set(cast(list[str], replay["result_plan_digests"]))) == 1,
        "commits_effective_once": bool(baseline["all_commits_inserted"])
        and bool(replay["all_commits_inserted"]),
        "trusted_results_match": baseline["result_statuses"]
        == replay["result_statuses"]
        == ["passed", "passed", "passed"],
    }
    summary: dict[str, object] = {
        "schema_version": "verirun.distributed-smoke/v1",
        "generated_at": datetime.now(UTC),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "ray": version("ray"),
            "reference_workload": "cpu-trusted-fixtures",
        },
        "source": source_state(),
        "baseline": baseline,
        "replay": replay,
        "checks": checks,
    }
    output.mkdir(parents=True, exist_ok=True)
    write_canonical_json(output / "summary.json", summary)
    return summary


def distributed_smoke_succeeded(summary: dict[str, object]) -> bool:
    """Return whether every explicitly recorded M4 local contract check passed."""

    checks = summary["checks"]
    return isinstance(checks, dict) and all(value is True for value in checks.values())


class _FailFirstCommit:
    """Fixture-only one-shot storage outage after Ray computed a valid payload."""

    def __init__(self, plane: InMemoryControlPlane) -> None:
        self._plane = plane
        self._failed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._plane, name)

    def commit_result(self, *args: Any, **kwargs: Any) -> Any:
        if not self._failed:
            self._failed = True
            raise OSError("trusted fixture transient storage outage")
        return self._plane.commit_result(*args, **kwargs)


def _fault_executor(*, max_in_flight: int) -> BoundedRayExecutor:
    return BoundedRayExecutor(
        RayExecutionConfig(
            max_in_flight=max_in_flight,
            lease_seconds=30,
            resource_pools=(
                ResourcePool(
                    resource_class=ResourceClass.CPU_VERIFIER,
                    ray_resource="verifier_cpu",
                ),
            ),
        )
    )


def _recovered_after_crash(
    *,
    run_id: str,
    actor: bool,
) -> dict[str, object]:
    plane, plan_digest = _new_plane(run_id=run_id)
    executor = _fault_executor(max_in_flight=1)
    try:
        if actor:
            executor.execute_run_with_actor(
                plane,
                run_id=run_id,
                worker_id=f"{run_id}-crash",
                operation=crashing_fixture_operation,
            )
        else:
            executor.execute_run(
                plane,
                run_id=run_id,
                worker_id=f"{run_id}-crash",
                operation=crashing_fixture_operation,
            )
    except DistributedExecutionError as exc:
        initial_error = str(exc)
    else:
        initial_error = "missing Ray failure"
    # Advance only the fixture's in-memory control-plane clock, never wall time.
    reclaimed = plane.reclaim_expired(now=datetime.now(UTC) + timedelta(seconds=31))
    outcomes = executor.execute_run(
        plane,
        run_id=run_id,
        worker_id=f"{run_id}-recovery",
        operation=trusted_fixture_operation,
    )
    run, _ = plane.inspect_run(run_id)
    results = plane.list_results(run_id)
    return {
        "initial_error": initial_error,
        "reclaimed": reclaimed,
        "plan_digest": plan_digest,
        "run_state": run.state.value,
        "result_count": len(results),
        "all_commits_inserted": all(outcome.inserted for outcome in outcomes),
        "result_plan_digests": [result.verification_plan_digest for result in results],
    }


def _recovered_after_transient_commit(*, run_id: str) -> dict[str, object]:
    plane, plan_digest = _new_plane(run_id=run_id)
    executor = _fault_executor(max_in_flight=1)
    try:
        executor.execute_run(
            cast(ControlPlaneExecutionBackend, _FailFirstCommit(plane)),
            run_id=run_id,
            worker_id=f"{run_id}-storage-outage",
            operation=trusted_fixture_operation,
        )
    except DistributedExecutionError as exc:
        initial_error = str(exc)
    else:
        initial_error = "missing durable commit failure"
    reclaimed = plane.reclaim_expired(now=datetime.now(UTC) + timedelta(seconds=31))
    outcomes = executor.execute_run(
        plane,
        run_id=run_id,
        worker_id=f"{run_id}-storage-recovery",
        operation=trusted_fixture_operation,
    )
    run, _ = plane.inspect_run(run_id)
    results = plane.list_results(run_id)
    return {
        "initial_error": initial_error,
        "reclaimed": reclaimed,
        "plan_digest": plan_digest,
        "run_state": run.state.value,
        "result_count": len(results),
        "all_commits_inserted": all(outcome.inserted for outcome in outcomes),
        "result_plan_digests": [result.verification_plan_digest for result in results],
    }


def _run_straggler_fixture(*, run_id: str) -> dict[str, object]:
    plane, plan_digest = _new_plane(run_id=run_id)
    started_at = time.perf_counter()
    outcomes = _fault_executor(max_in_flight=2).execute_run(
        plane,
        run_id=run_id,
        worker_id=f"{run_id}-driver",
        operation=straggler_fixture_operation,
    )
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    run, _ = plane.inspect_run(run_id)
    results = plane.list_results(run_id)
    return {
        "elapsed_ms": elapsed_ms,
        "plan_digest": plan_digest,
        "run_state": run.state.value,
        "result_count": len(results),
        "all_commits_inserted": all(outcome.inserted for outcome in outcomes),
        "delay_seconds": [result.result_payload["delay_seconds"] for result in results],
        "result_plan_digests": [result.verification_plan_digest for result in results],
    }


def _object_store_spilled_bytes(metrics_text: str) -> int:
    """Return the strongest current per-node Ray spill gauge in the exporter."""

    object_store_total = 0.0
    spill_manager_total = 0.0
    for line in metrics_text.splitlines():
        if not line.startswith(("ray_object_store_memory{", "ray_spill_manager_objects_bytes{")):
            continue
        try:
            sample = float(line.rsplit(maxsplit=1)[1])
        except (IndexError, ValueError):
            continue
        if line.startswith("ray_object_store_memory{") and (
            'Location="SPILLED"' in line or 'ObjectState="SPILLED"' in line
        ):
            object_store_total += sample
        if line.startswith("ray_spill_manager_objects_bytes{") and 'State="Spilled"' in line:
            spill_manager_total += sample
    return int(max(object_store_total, spill_manager_total))


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"expected numeric fixture value, got {type(value).__name__}")
    return int(value)


def _ray_object_store_metrics(ray: Any) -> dict[str, object]:
    """Read per-node Ray Prometheus endpoints without treating scrape failure as spill."""

    samples: list[dict[str, object]] = []
    for node in cast(Iterable[dict[str, object]], ray.nodes()):
        address = node.get("NodeManagerAddress")
        if not isinstance(address, str) or not address:
            continue
        url = f"http://{address}:8080/metrics"
        try:
            with urlopen(url, timeout=5) as response:
                metrics_text = response.read().decode("utf-8", errors="replace")
        except (OSError, URLError) as exc:
            samples.append({"address": address, "error": str(exc), "spilled_bytes": 0})
            continue
        samples.append(
            {
                "address": address,
                "spilled_bytes": _object_store_spilled_bytes(metrics_text),
            }
        )
    return {
        "samples": samples,
        "spilled_bytes": sum(_integer(sample["spilled_bytes"]) for sample in samples),
        "all_endpoints_scraped": bool(samples) and all("error" not in sample for sample in samples),
    }


def _large_object_payload(size_bytes: int, marker: int) -> bytes:
    return b"v" * (size_bytes - 1) + bytes([marker])


def _run_large_object_probe(ray: Any) -> dict[str, object]:
    """Retain enough driver-owned objects to require spill on the M4 100 MB store."""

    size_bytes = 40_000_000
    # ``ray.put`` retains the driver's references in one object store. Task returns
    # can be released before a driver consumes them, which is unsuitable for a
    # deterministic spill probe.
    references = [ray.put(_large_object_payload(size_bytes, marker)) for marker in range(6)]
    # ``ray.wait`` may restore a spilled input. Poll the gauge before any wait/get
    # operation so the evidence records the actual spill rather than the restored
    # post-consumption state.
    metrics_snapshots = []
    for _ in range(20):
        metrics = _ray_object_store_metrics(ray)
        metrics_snapshots.append(metrics)
        if _integer(metrics["spilled_bytes"]) > 0:
            break
        time.sleep(0.25)
    payload_sizes = [len(payload) for payload in ray.get(references)]
    # Ray 2.50 updates its spill-manager gauge asynchronously. Preserve both
    # pre-consumption and post-consumption samples instead of assuming a fixed
    # exporter refresh order.
    for _ in range(20):
        metrics = _ray_object_store_metrics(ray)
        metrics_snapshots.append(metrics)
        if _integer(metrics["spilled_bytes"]) > 0:
            break
        time.sleep(0.25)
    metrics = max(
        metrics_snapshots,
        key=lambda snapshot: _integer(snapshot["spilled_bytes"]),
    )
    del references
    gc.collect()
    return {
        "payload_size_bytes": size_bytes,
        "payload_count": len(payload_sizes),
        "payload_sizes": payload_sizes,
        "metrics": metrics,
        "metrics_poll_count": len(metrics_snapshots),
    }


def _fault_recovery_checks(result: dict[str, object]) -> bool:
    return (
        "lease remains reclaimable" in str(result["initial_error"])
        and _integer(result["reclaimed"]) == 1
        and result["run_state"] == "completed"
        and _integer(result["result_count"]) == 3
        and bool(result["all_commits_inserted"])
        and len(set(cast(list[str], result["result_plan_digests"]))) == 1
    )


def run_distributed_fault_smoke(output: Path) -> dict[str, object]:
    """Exercise M4 faults in the current local Ray or KubeRay RayJob cluster."""

    ray = _ray()
    _initialize_ray(ray)
    try:
        task_crash = _recovered_after_crash(run_id="m4-task-crash", actor=False)
        actor_crash = _recovered_after_crash(run_id="m4-actor-crash", actor=True)
        storage_transient = _recovered_after_transient_commit(run_id="m4-storage-transient")
        straggler = _run_straggler_fixture(run_id="m4-straggler")
        large_object = _run_large_object_probe(ray)
    finally:
        ray.shutdown()

    metrics = cast(dict[str, object], large_object["metrics"])
    checks = {
        "task_crash_recovered": _fault_recovery_checks(task_crash),
        "actor_crash_recovered": _fault_recovery_checks(actor_crash),
        "storage_transient_recovered": _fault_recovery_checks(storage_transient),
        "straggler_completed": straggler["run_state"] == "completed"
        and _integer(straggler["result_count"]) == 3
        and bool(straggler["all_commits_inserted"])
        and set(cast(list[float], straggler["delay_seconds"])) == {0.0, 0.2},
        "large_object_payloads_returned": large_object["payload_sizes"] == [40_000_000] * 6,
        "spill_metrics_scraped": bool(metrics["all_endpoints_scraped"]),
        "spill_observed": _integer(metrics["spilled_bytes"]) > 0,
    }
    summary: dict[str, object] = {
        "schema_version": "verirun.distributed-fault-smoke/v1",
        "generated_at": datetime.now(UTC),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "ray": version("ray"),
            "reference_workload": "cpu-trusted-fixtures",
        },
        "source": source_state(),
        "task_crash": task_crash,
        "actor_crash": actor_crash,
        "storage_transient": storage_transient,
        "straggler": straggler,
        "large_object": large_object,
        "checks": checks,
    }
    output.mkdir(parents=True, exist_ok=True)
    write_canonical_json(output / "summary.json", summary)
    return summary


def distributed_fault_smoke_succeeded(summary: dict[str, object]) -> bool:
    checks = summary["checks"]
    return isinstance(checks, dict) and all(value is True for value in checks.values())


def _p95(values: list[int]) -> int:
    if not values:
        raise ValueError("p95 requires at least one sample")
    return sorted(values)[min(len(values) - 1, int(len(values) * 0.95))]


def _driver_max_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if platform.system() == "Darwin" else value * 1024


def run_distributed_concurrency_matrix(output: Path) -> dict[str, object]:
    """Measure logical 1/2/4/8/16 fixture concurrency without capacity claims."""

    ray = _ray()
    rows: list[dict[str, object]] = []
    for concurrency in (1, 2, 4, 8, 16):
        ray.init(
            num_cpus=concurrency,
            resources={"verifier_cpu": concurrency},
            include_dashboard=False,
            ignore_reinit_error=True,
        )
        try:
            run_id = f"m4-concurrency-{concurrency}"
            plane, plan_digest = _new_plane(run_id=run_id, candidate_count=16)
            started_at = time.perf_counter()
            outcomes = _fault_executor(max_in_flight=concurrency).execute_run(
                plane,
                run_id=run_id,
                worker_id=f"{run_id}-driver",
                operation=timed_fixture_operation,
            )
            elapsed_seconds = time.perf_counter() - started_at
            run, _ = plane.inspect_run(run_id)
            results = plane.list_results(run_id)
        finally:
            ray.shutdown()
        durations = [_integer(result.result_payload["worker_duration_ms"]) for result in results]
        rows.append(
            {
                "logical_concurrency": concurrency,
                "task_count": len(results),
                "elapsed_ms": int(elapsed_seconds * 1000),
                "throughput_tasks_per_second": round(len(results) / elapsed_seconds, 3),
                "p95_worker_duration_ms": _p95(durations),
                "driver_max_rss_bytes": _driver_max_rss_bytes(),
                "spill_bytes": 0,
                "recovery_events": 0,
                "plan_digest": plan_digest,
                "run_state": run.state.value,
                "all_commits_inserted": all(outcome.inserted for outcome in outcomes),
            }
        )
    checks = {
        "all_requested_concurrency_present": [row["logical_concurrency"] for row in rows]
        == [1, 2, 4, 8, 16],
        "all_runs_completed": all(row["run_state"] == "completed" for row in rows),
        "all_commits_inserted": all(bool(row["all_commits_inserted"]) for row in rows),
        "all_task_counts_match": all(_integer(row["task_count"]) == 16 for row in rows),
    }
    summary: dict[str, object] = {
        "schema_version": "verirun.distributed-concurrency/v1",
        "generated_at": datetime.now(UTC),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "ray": version("ray"),
            "reference_workload": "cpu-trusted-fixtures",
            "claim_boundary": "logical scheduler fixture; not CPU capacity evidence",
        },
        "source": source_state(),
        "rows": rows,
        "checks": checks,
    }
    output.mkdir(parents=True, exist_ok=True)
    write_canonical_json(output / "summary.json", summary)
    return summary


def distributed_concurrency_succeeded(summary: dict[str, object]) -> bool:
    checks = summary["checks"]
    return isinstance(checks, dict) and all(value is True for value in checks.values())
