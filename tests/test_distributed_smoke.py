from __future__ import annotations

import pytest

from verirun.distributed_smoke import (
    _initialize_ray,
    _object_store_spilled_bytes,
    distributed_concurrency_succeeded,
    distributed_fault_smoke_succeeded,
    distributed_smoke_succeeded,
    run_distributed_concurrency_matrix,
    run_distributed_fault_smoke,
    run_distributed_smoke,
)


class _FakeRay:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def init(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def test_initialize_ray_joins_rayjob_cluster_without_local_resources(monkeypatch) -> None:
    monkeypatch.setenv("RAY_ADDRESS", "ray://ray-head:10001")
    ray = _FakeRay()

    _initialize_ray(ray)

    assert ray.calls == [{"address": "ray://ray-head:10001", "ignore_reinit_error": True}]


def test_initialize_ray_starts_the_local_named_resource_fixture(monkeypatch) -> None:
    monkeypatch.delenv("RAY_ADDRESS", raising=False)
    ray = _FakeRay()

    _initialize_ray(ray)

    assert ray.calls == [
        {
            "num_cpus": 2,
            "resources": {"verifier_cpu": 2},
            "include_dashboard": False,
            "ignore_reinit_error": True,
        }
    ]


def test_object_store_spill_parser_ignores_non_spilled_and_invalid_samples() -> None:
    metrics = """\
ray_object_store_memory{Location=\"MMAP_SHM\",ObjectState=\"SEALED\"} 128
ray_object_store_memory{Location=\"SPILLED\",ObjectState=\"SEALED\"} 42.9
ray_object_store_memory{Location=\"MMAP_DISK\",ObjectState=\"SPILLED\"} 17
ray_object_store_memory{Location=\"SPILLED\"} not-a-number
ray_spill_manager_objects_bytes{State=\"Spilled\"} 240000018
"""

    assert _object_store_spilled_bytes(metrics) == 240000018


def test_distributed_smoke_replays_cpu_trusted_fixtures(tmp_path) -> None:
    pytest.importorskip("ray")

    summary = run_distributed_smoke(tmp_path / "distributed-smoke")

    assert distributed_smoke_succeeded(summary)
    assert (tmp_path / "distributed-smoke" / "summary.json").is_file()


def test_fault_smoke_records_reclaimable_failures_and_explicit_spill_status(tmp_path) -> None:
    """Local Ray covers recovery semantics; live KubeRay is the spill authority."""

    pytest.importorskip("ray")

    summary = run_distributed_fault_smoke(tmp_path / "distributed-fault-smoke")

    checks = summary["checks"]
    assert isinstance(checks, dict)
    assert checks["task_crash_recovered"] is True
    assert checks["actor_crash_recovered"] is True
    assert checks["storage_transient_recovered"] is True
    assert checks["straggler_completed"] is True
    assert checks["large_object_payloads_returned"] is True
    assert isinstance(checks["spill_metrics_scraped"], bool)
    assert isinstance(checks["spill_observed"], bool)
    assert isinstance(distributed_fault_smoke_succeeded(summary), bool)
    assert (tmp_path / "distributed-fault-smoke" / "summary.json").is_file()


def test_concurrency_matrix_replays_logical_cpu_trusted_fixtures(tmp_path) -> None:
    pytest.importorskip("ray")

    summary = run_distributed_concurrency_matrix(tmp_path / "distributed-concurrency")

    assert distributed_concurrency_succeeded(summary)
    assert [row["logical_concurrency"] for row in summary["rows"]] == [1, 2, 4, 8, 16]
    assert (tmp_path / "distributed-concurrency" / "summary.json").is_file()
