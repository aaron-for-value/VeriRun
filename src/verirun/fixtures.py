"""Trusted synthetic fixtures for the v0.1 protocol smoke test."""

from __future__ import annotations

from dataclasses import dataclass

from verirun.artifacts import ArtifactStore
from verirun.canonical import content_hash
from verirun.models import (
    BenchmarkSpec,
    Candidate,
    EvalManifest,
    ExecutionSpec,
    GenerationSpec,
    ModelSpec,
    VerificationStatus,
    VerifierSpec,
)


@dataclass(frozen=True)
class SmokeCase:
    name: str
    task_id: str
    candidate: str
    tests: str
    expected_status: VerificationStatus


def smoke_cases() -> tuple[SmokeCase, ...]:
    boundary_candidate = """def is_even(value: int) -> bool:
    return value > 0 and value % 2 == 0
"""
    return (
        SmokeCase(
            name="oracle-plus",
            task_id="Synthetic/IsEven/oracle-plus",
            candidate="""def is_even(value: int) -> bool:
    return value % 2 == 0
""",
            tests="""assert is_even(2) is True
assert is_even(3) is False
assert is_even(0) is True
assert is_even(-2) is True
""",
            expected_status=VerificationStatus.PASSED,
        ),
        SmokeCase(
            name="boundary-base",
            task_id="Synthetic/IsEven/boundary-base",
            candidate=boundary_candidate,
            tests="""assert is_even(2) is True
assert is_even(3) is False
""",
            expected_status=VerificationStatus.PASSED,
        ),
        SmokeCase(
            name="boundary-plus",
            task_id="Synthetic/IsEven/boundary-plus",
            candidate=boundary_candidate,
            tests="""assert is_even(2) is True
assert is_even(3) is False
assert is_even(0) is True
assert is_even(-2) is True
""",
            expected_status=VerificationStatus.TEST_FAILURE,
        ),
        SmokeCase(
            name="compile-error",
            task_id="Synthetic/CompileError",
            candidate="def broken(:\n    return 1\n",
            tests="assert broken() == 1\n",
            expected_status=VerificationStatus.COMPILE_ERROR,
        ),
        SmokeCase(
            name="timeout",
            task_id="Synthetic/Timeout",
            candidate="""def never_returns() -> None:
    while True:
        pass
""",
            tests="never_returns()\n",
            expected_status=VerificationStatus.TIMEOUT,
        ),
    )


def build_smoke_manifest(case: SmokeCase, store: ArtifactStore) -> EvalManifest:
    candidate_ref = store.put_text(
        kind="candidate", text=case.candidate, media_type="text/x-python"
    )
    test_ref = store.put_text(kind="tests", text=case.tests, media_type="text/x-python")
    dataset_identity = content_hash(
        [
            {
                "name": item.name,
                "task_id": item.task_id,
                "candidate": item.candidate,
                "tests": item.tests,
            }
            for item in smoke_cases()
        ]
    )
    benchmark = BenchmarkSpec(
        name="verirun-synthetic",
        version="v0.1",
        dataset_hash=f"sha256:{dataset_identity}",
        split="smoke",
        subset_label="trusted-synthetic-v0.1",
        task_ids=(case.task_id,),
        standard_protocol=False,
    )
    return EvalManifest(
        run_id=f"smoke-{case.name}",
        benchmark=benchmark,
        model=ModelSpec(endpoint_type="frozen", model_revision="trusted-fixture-v0.1"),
        generation=GenerationSpec(),
        candidate=Candidate(
            candidate_id=f"{case.name}-candidate",
            task_id=case.task_id,
            source=candidate_ref,
        ),
        verifier=VerifierSpec(
            adapter="verirun.synthetic",
            version="v0.1",
            tests=test_ref,
            timeout_seconds=0.25 if case.name == "timeout" else 2.0,
            max_output_bytes=4096,
        ),
        execution=ExecutionSpec(),
    )
