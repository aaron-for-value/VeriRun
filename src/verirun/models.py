"""Immutable v0.1 protocol models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
DatasetDigest = Annotated[
    str,
    Field(pattern=r"^(?:md5:[0-9a-f]{32}|sha256:[0-9a-f]{64})$"),
]
NonEmpty = Annotated[str, Field(min_length=1)]
ContainerImage = Annotated[str, Field(pattern=r"^.+@sha256:[0-9a-f]{64}$")]


class FrozenModel(BaseModel):
    """Base model for immutable, closed protocol records."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class VerificationStatus(StrEnum):
    PASSED = "passed"
    COMPILE_ERROR = "compile_error"
    TEST_FAILURE = "test_failure"
    TIMEOUT = "timeout"
    OOM = "oom"
    POLICY_VIOLATION = "policy_violation"
    INFRA_ERROR = "infra_error"


class AttemptState(StrEnum):
    RUNNING = "running"
    FINISHED = "finished"


class ArtifactRef(FrozenModel):
    kind: NonEmpty
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]
    media_type: NonEmpty
    relative_path: NonEmpty

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("artifact path must be relative and cannot traverse parents")
        return path.as_posix()


class BenchmarkSpec(FrozenModel):
    name: NonEmpty
    version: NonEmpty
    dataset_hash: DatasetDigest
    split: NonEmpty
    subset_label: str | None = None
    task_ids: tuple[NonEmpty, ...]
    standard_protocol: bool

    @model_validator(mode="after")
    def validate_subset_claim(self) -> BenchmarkSpec:
        if not self.standard_protocol and not self.subset_label:
            raise ValueError("non-standard benchmark runs require a subset_label")
        return self


class ModelSpec(FrozenModel):
    endpoint_type: NonEmpty
    model_revision: NonEmpty
    tokenizer_revision: str | None = None


class GenerationSpec(FrozenModel):
    temperature: Annotated[float, Field(ge=0)] = 0.0
    top_p: Annotated[float, Field(gt=0, le=1)] = 1.0
    max_tokens: Annotated[int, Field(gt=0)] = 1
    n: Annotated[int, Field(gt=0)] = 1
    seed: int = 0


class Candidate(FrozenModel):
    candidate_id: NonEmpty
    task_id: NonEmpty
    language: Literal["python"] = "python"
    source: ArtifactRef


class VerifierSpec(FrozenModel):
    adapter: NonEmpty
    version: NonEmpty
    tests: ArtifactRef
    timeout_seconds: Annotated[float, Field(gt=0)]
    max_output_bytes: Annotated[int, Field(gt=0)] = 65_536
    image_digest: str | None = None


class ExecutionSpec(FrozenModel):
    engine: Literal["local", "container", "kubernetes"] = "local"
    concurrency: Annotated[int, Field(gt=0)] = 1
    retry_policy: Literal["none"] = "none"
    sandbox_policy: Literal[
        "trusted-fixtures-only", "development-container", "kubernetes-gvisor"
    ] = "trusted-fixtures-only"
    container_image: ContainerImage | None = None
    container_cpus: Annotated[float, Field(gt=0)] | None = None
    container_memory_mb: Annotated[int, Field(gt=0)] | None = None
    container_pids_limit: Annotated[int, Field(gt=0)] | None = None
    kubernetes_context: NonEmpty | None = None
    kubernetes_namespace: NonEmpty | None = None
    kubernetes_runtime_class: NonEmpty | None = None

    @model_validator(mode="after")
    def validate_execution_tier(self) -> ExecutionSpec:
        container_fields = (
            self.container_image,
            self.container_cpus,
            self.container_memory_mb,
            self.container_pids_limit,
        )
        kubernetes_fields = (
            self.kubernetes_context,
            self.kubernetes_namespace,
            self.kubernetes_runtime_class,
        )
        if self.engine == "local":
            if self.sandbox_policy != "trusted-fixtures-only":
                raise ValueError("local engine requires trusted-fixtures-only policy")
            if any(value is not None for value in container_fields):
                raise ValueError("local engine cannot declare container settings")
            if any(value is not None for value in kubernetes_fields):
                raise ValueError("local engine cannot declare Kubernetes settings")
            return self
        if self.engine == "container":
            if self.sandbox_policy != "development-container":
                raise ValueError("container engine requires development-container policy")
            if any(value is None for value in container_fields):
                raise ValueError("container engine requires image, CPU, memory, and PID limits")
            if any(value is not None for value in kubernetes_fields):
                raise ValueError("container engine cannot declare Kubernetes settings")
            return self
        if self.sandbox_policy != "kubernetes-gvisor":
            raise ValueError("kubernetes engine requires kubernetes-gvisor policy")
        if any(value is None for value in container_fields[:3]):
            raise ValueError("kubernetes engine requires image, CPU, and memory limits")
        if self.container_pids_limit is not None:
            raise ValueError("kubernetes engine cannot declare a Docker PID limit")
        if any(value is None for value in kubernetes_fields):
            raise ValueError("kubernetes engine requires context, namespace, and RuntimeClass")
        return self


class EvalManifest(FrozenModel):
    schema_version: Literal["verirun.eval-manifest/v1"] = "verirun.eval-manifest/v1"
    run_id: NonEmpty
    benchmark: BenchmarkSpec
    model: ModelSpec
    generation: GenerationSpec
    candidate: Candidate
    verifier: VerifierSpec
    execution: ExecutionSpec

    @model_validator(mode="after")
    def validate_task_identity(self) -> EvalManifest:
        if self.candidate.task_id not in self.benchmark.task_ids:
            raise ValueError("candidate task_id is absent from benchmark task_ids")
        if self.execution.engine in {"container", "kubernetes"}:
            assert self.execution.container_image is not None
            image_digest = self.execution.container_image.rsplit("@", maxsplit=1)[1]
            if self.verifier.image_digest != image_digest:
                raise ValueError("isolated verifier image digest must match execution image")
        return self


class TaskAttempt(FrozenModel):
    attempt_id: NonEmpty
    run_id: NonEmpty
    task_id: NonEmpty
    candidate_id: NonEmpty
    verification_plan_id: NonEmpty | None = None
    verification_plan_digest: Sha256 | None = None
    state: AttemptState
    started_at: datetime
    finished_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> TaskAttempt:
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("attempt timestamps must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("attempt finished_at cannot precede started_at")
        if (self.verification_plan_id is None) != (self.verification_plan_digest is None):
            raise ValueError("verification plan ID and digest must be declared together")
        return self


class VerificationResult(FrozenModel):
    schema_version: Literal["verirun.verification-result/v1"] = "verirun.verification-result/v1"
    attempt: TaskAttempt
    status: VerificationStatus
    error_class: str | None = None
    error_message: str | None = None
    candidate_hash: Sha256
    test_hash: Sha256
    verifier_version: NonEmpty
    exit_code: int | None = None
    duration_ms: Annotated[int, Field(ge=0)]
    stdout: ArtifactRef
    stderr: ArtifactRef
    output_truncated: bool = False

    @model_validator(mode="after")
    def validate_error(self) -> VerificationResult:
        if self.status is VerificationStatus.PASSED and self.error_class is not None:
            raise ValueError("passed results cannot carry an error_class")
        if self.status is not VerificationStatus.PASSED and self.error_class is None:
            raise ValueError("failed results require an error_class")
        return self

    def semantic_payload(self) -> dict[str, object]:
        """Return stable fields used to compare independent replay attempts."""

        return {
            "schema_version": self.schema_version,
            "run_id": self.attempt.run_id,
            "task_id": self.attempt.task_id,
            "candidate_id": self.attempt.candidate_id,
            "verification_plan_id": self.attempt.verification_plan_id,
            "verification_plan_digest": self.attempt.verification_plan_digest,
            "status": self.status.value,
            "error_class": self.error_class,
            "error_message": self.error_message,
            "candidate_hash": self.candidate_hash,
            "test_hash": self.test_hash,
            "verifier_version": self.verifier_version,
            "exit_code": self.exit_code,
            "stdout_hash": self.stdout.sha256,
            "stderr_hash": self.stderr.sha256,
            "output_truncated": self.output_truncated,
        }


class ReplayComparison(FrozenModel):
    schema_version: Literal["verirun.replay-comparison/v1"] = "verirun.replay-comparison/v1"
    baseline_result_hash: Sha256
    replay_result_hash: Sha256
    baseline_semantic_hash: Sha256
    replay_semantic_hash: Sha256
    matched: bool
    differing_fields: tuple[str, ...] = ()
