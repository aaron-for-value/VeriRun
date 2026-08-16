from __future__ import annotations

import pytest
from pydantic import ValidationError

from verirun.models import ArtifactRef, BenchmarkSpec, VerificationStatus


def artifact() -> ArtifactRef:
    return ArtifactRef(
        kind="candidate",
        sha256="a" * 64,
        size_bytes=1,
        media_type="text/plain",
        relative_path=f"sha256/aa/{'a' * 64}",
    )


def test_models_are_immutable() -> None:
    reference = artifact()

    with pytest.raises(ValidationError, match="frozen"):
        reference.kind = "tests"  # type: ignore[misc]


def test_artifact_path_cannot_escape_store() -> None:
    with pytest.raises(ValidationError, match="cannot traverse"):
        ArtifactRef(
            kind="candidate",
            sha256="a" * 64,
            size_bytes=1,
            media_type="text/plain",
            relative_path="../candidate.py",
        )


def test_subset_requires_label() -> None:
    with pytest.raises(ValidationError, match="subset_label"):
        BenchmarkSpec(
            name="benchmark",
            version="1",
            dataset_hash=f"sha256:{'b' * 64}",
            split="test",
            task_ids=("Task/0",),
            standard_protocol=False,
        )


def test_status_values_are_stable() -> None:
    assert {status.value for status in VerificationStatus} == {
        "passed",
        "compile_error",
        "test_failure",
        "timeout",
        "oom",
        "policy_violation",
        "infra_error",
    }


@pytest.mark.parametrize("algorithm,length", [("md5", 32), ("sha256", 64)])
def test_benchmark_digest_records_algorithm(algorithm: str, length: int) -> None:
    benchmark = BenchmarkSpec(
        name="benchmark",
        version="1",
        dataset_hash=f"{algorithm}:{'b' * length}",
        split="test",
        subset_label="subset",
        task_ids=("Task/0",),
        standard_protocol=False,
    )

    assert benchmark.dataset_hash.startswith(f"{algorithm}:")


def test_benchmark_digest_rejects_unqualified_hash() -> None:
    with pytest.raises(ValidationError, match="dataset_hash"):
        BenchmarkSpec(
            name="benchmark",
            version="1",
            dataset_hash="b" * 64,
            split="test",
            subset_label="subset",
            task_ids=("Task/0",),
            standard_protocol=False,
        )
