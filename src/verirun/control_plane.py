"""Durable-control-plane domain contracts and deterministic in-memory reference backend."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Annotated, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from verirun.canonical import content_hash
from verirun.models import FrozenModel, NonEmpty, Sha256


class ControlPlaneError(RuntimeError):
    """Base class for stable control-plane failures."""


class PlanCompilationError(ControlPlaneError):
    """Raised when deterministic plan inputs cannot produce a valid plan."""


class InvalidTransitionError(ControlPlaneError):
    """Raised when a plan or run state transition is illegal."""


class PlanNotSchedulableError(ControlPlaneError):
    """Raised when work is submitted against a non-frozen plan."""


class IdempotencyConflictError(ControlPlaneError):
    """Raised when one idempotency key is reused for different intent."""


class LeaseConflictError(ControlPlaneError):
    """Raised when a worker does not own the current live lease."""


class LateAttemptError(ControlPlaneError):
    """Raised when an expired or superseded attempt tries to commit."""


class FinalResultConflictError(ControlPlaneError):
    """Raised when a completed task receives a different final result."""


class MixedCohortError(ControlPlaneError):
    """Raised when aggregation would mix verification-plan digests."""


class PlanState(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    FROZEN = "frozen"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


class RunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class RunTaskState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class LeaseState(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class FailureDomain(StrEnum):
    PLAN = "plan"
    MODEL = "model"
    USER_CODE = "user_code"
    VERIFIER = "verifier"
    SANDBOX = "sandbox"
    SCHEDULER = "scheduler"
    STORAGE = "storage"


class TaskSpec(FrozenModel):
    task_id: NonEmpty
    task_family: NonEmpty
    verifier_tags: tuple[NonEmpty, ...] = ()


class EvaluationIntent(FrozenModel):
    name: NonEmpty
    required_verifier_tags: tuple[NonEmpty, ...] = ()


class VerifierCatalogEntry(FrozenModel):
    verifier_id: NonEmpty
    task_families: tuple[NonEmpty, ...]
    intents: tuple[NonEmpty, ...]
    adapter: NonEmpty
    version: NonEmpty
    config_digest: Sha256
    image_digest: Sha256
    required_evidence: tuple[NonEmpty, ...]
    tags: tuple[NonEmpty, ...] = ()
    expected_model_tokens: Annotated[int, Field(ge=0)] = 0
    expected_sandbox_cpu_seconds: Annotated[float, Field(ge=0)] = 0.0
    priority: int = 0


class VerifierNode(FrozenModel):
    verifier_id: NonEmpty
    adapter: NonEmpty
    version: NonEmpty
    config_digest: Sha256
    image_digest: Sha256
    required_evidence: tuple[NonEmpty, ...]
    expected_model_tokens: Annotated[int, Field(ge=0)]
    expected_sandbox_cpu_seconds: Annotated[float, Field(ge=0)]


class AggregationPolicy(FrozenModel):
    policy_id: NonEmpty
    version: NonEmpty
    reducer: NonEmpty
    config_digest: Sha256


class BudgetEstimate(FrozenModel):
    candidate_count: Annotated[int, Field(gt=0)]
    expected_model_tokens: Annotated[int, Field(ge=0)]
    expected_sandbox_cpu_seconds: Annotated[float, Field(ge=0)]
    max_concurrency: Annotated[int, Field(gt=0)]


class PlanCompileRequest(FrozenModel):
    task_spec: TaskSpec
    evaluation_intent: EvaluationIntent
    verifier_catalog: tuple[VerifierCatalogEntry, ...]
    aggregation_policy: AggregationPolicy
    policy_revision: NonEmpty
    input_evidence_digests: tuple[Sha256, ...] = ()
    comparison_cohort_id: NonEmpty
    candidate_count: Annotated[int, Field(gt=0)]
    max_concurrency: Annotated[int, Field(gt=0)]


class VerificationPlanSpec(FrozenModel):
    schema_version: Literal["verirun.verification-plan/v1"] = "verirun.verification-plan/v1"
    task_spec: TaskSpec
    evaluation_intent: EvaluationIntent
    verifier_graph: tuple[VerifierNode, ...]
    required_evidence: tuple[NonEmpty, ...]
    aggregation_policy: AggregationPolicy
    policy_revision: NonEmpty
    input_evidence_digests: tuple[Sha256, ...]
    comparison_cohort_id: NonEmpty
    budget_estimate: BudgetEstimate


class VerificationPlan(FrozenModel):
    schema_version: Literal["verirun.verification-plan-record/v1"] = (
        "verirun.verification-plan-record/v1"
    )
    plan_id: NonEmpty
    revision: Annotated[int, Field(gt=0)]
    plan_digest: Sha256
    spec: VerificationPlanSpec
    state: PlanState
    created_at: datetime
    updated_at: datetime
    state_reason: str | None = None

    @model_validator(mode="after")
    def validate_record(self) -> VerificationPlan:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("plan timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("plan updated_at cannot precede created_at")
        if content_hash(self.spec) != self.plan_digest:
            raise ValueError("plan_digest does not match the canonical plan spec")
        return self


class RunTaskInput(FrozenModel):
    task_id: NonEmpty
    candidate_id: NonEmpty
    candidate_hash: Sha256


class CreateRunCommand(FrozenModel):
    schema_version: Literal["verirun.create-run-command/v1"] = "verirun.create-run-command/v1"
    idempotency_key: NonEmpty
    run_id: NonEmpty
    requested_cohort_id: NonEmpty
    plan_id: NonEmpty
    plan_revision: Annotated[int, Field(gt=0)]
    plan_digest: Sha256
    tasks: tuple[RunTaskInput, ...]
    source_run_id: str | None = None

    @field_validator("tasks")
    @classmethod
    def validate_tasks(cls, value: tuple[RunTaskInput, ...]) -> tuple[RunTaskInput, ...]:
        if not value:
            raise ValueError("a run requires at least one task")
        identities = {(task.task_id, task.candidate_id) for task in value}
        if len(identities) != len(value):
            raise ValueError("run tasks must have unique task/candidate identities")
        return value


class EvalRunRecord(FrozenModel):
    schema_version: Literal["verirun.eval-run/v1"] = "verirun.eval-run/v1"
    run_id: NonEmpty
    requested_cohort_id: NonEmpty
    cohort_id: NonEmpty
    plan_id: NonEmpty
    plan_revision: Annotated[int, Field(gt=0)]
    verification_plan_digest: Sha256
    state: RunState
    source_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class RunTaskRecord(FrozenModel):
    run_id: NonEmpty
    task_id: NonEmpty
    candidate_id: NonEmpty
    candidate_hash: Sha256
    verification_plan_id: NonEmpty
    verification_plan_digest: Sha256
    state: RunTaskState
    current_attempt_id: str | None = None


class RunInspection(FrozenModel):
    schema_version: Literal["verirun.run-inspection/v1"] = "verirun.run-inspection/v1"
    run: EvalRunRecord
    tasks: tuple[RunTaskRecord, ...]


class AttemptLease(FrozenModel):
    schema_version: Literal["verirun.attempt-lease/v1"] = "verirun.attempt-lease/v1"
    attempt_id: NonEmpty
    run_id: NonEmpty
    task_id: NonEmpty
    candidate_id: NonEmpty
    verification_plan_id: NonEmpty
    verification_plan_digest: Sha256
    worker_id: NonEmpty
    lease_token: NonEmpty
    state: LeaseState
    claimed_at: datetime
    heartbeat_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> AttemptLease:
        timestamps = (self.claimed_at, self.heartbeat_at, self.expires_at)
        if any(value.tzinfo is None for value in timestamps):
            raise ValueError("lease timestamps must be timezone-aware")
        if self.heartbeat_at < self.claimed_at or self.expires_at <= self.heartbeat_at:
            raise ValueError("lease timestamps are not monotonic")
        return self


class FinalResultRecord(FrozenModel):
    schema_version: Literal["verirun.final-result/v1"] = "verirun.final-result/v1"
    run_id: NonEmpty
    task_id: NonEmpty
    candidate_id: NonEmpty
    verification_plan_id: NonEmpty
    verification_plan_digest: Sha256
    attempt_id: NonEmpty
    result_digest: Sha256
    result_payload: dict[str, object]
    failure_domain: FailureDomain | None = None
    committed_at: datetime

    @field_validator("committed_at")
    @classmethod
    def validate_committed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("committed_at must be timezone-aware")
        return value


class CommitOutcome(FrozenModel):
    result: FinalResultRecord
    inserted: bool


class CommandReceipt(FrozenModel):
    idempotency_key: NonEmpty
    command_name: NonEmpty
    intent_digest: Sha256
    resource_id: NonEmpty
    created_at: datetime


class ArtifactMetadataRecord(FrozenModel):
    schema_version: Literal["verirun.artifact-metadata/v1"] = "verirun.artifact-metadata/v1"
    sha256: Sha256
    kind: NonEmpty
    size_bytes: Annotated[int, Field(ge=0)]
    media_type: NonEmpty
    storage_uri: NonEmpty
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("artifact created_at must be timezone-aware")
        return value


def compile_verification_plan(
    *,
    plan_id: str,
    revision: int,
    request: PlanCompileRequest,
    now: datetime | None = None,
) -> VerificationPlan:
    """Compile a plan from policy inputs that deliberately exclude candidate/model output."""

    selected = [
        entry
        for entry in request.verifier_catalog
        if request.task_spec.task_family in entry.task_families
        and request.evaluation_intent.name in entry.intents
        and (
            set(request.evaluation_intent.required_verifier_tags)
            | set(request.task_spec.verifier_tags)
        ).issubset(set(entry.tags))
    ]
    selected.sort(key=lambda entry: (entry.priority, entry.verifier_id))
    if not selected:
        raise PlanCompilationError("no verifier matches task family and evaluation intent")
    identifiers = [entry.verifier_id for entry in selected]
    if len(identifiers) != len(set(identifiers)):
        raise PlanCompilationError("verifier catalog contains duplicate selected verifier IDs")

    verifier_graph = tuple(
        VerifierNode(
            verifier_id=entry.verifier_id,
            adapter=entry.adapter,
            version=entry.version,
            config_digest=entry.config_digest,
            image_digest=entry.image_digest,
            required_evidence=tuple(sorted(set(entry.required_evidence))),
            expected_model_tokens=entry.expected_model_tokens,
            expected_sandbox_cpu_seconds=entry.expected_sandbox_cpu_seconds,
        )
        for entry in selected
    )
    required_evidence = tuple(
        sorted({evidence for node in verifier_graph for evidence in node.required_evidence})
    )
    budget = BudgetEstimate(
        candidate_count=request.candidate_count,
        expected_model_tokens=(
            sum(node.expected_model_tokens for node in verifier_graph) * request.candidate_count
        ),
        expected_sandbox_cpu_seconds=(
            sum(node.expected_sandbox_cpu_seconds for node in verifier_graph)
            * request.candidate_count
        ),
        max_concurrency=min(request.max_concurrency, request.candidate_count),
    )
    spec = VerificationPlanSpec(
        task_spec=request.task_spec,
        evaluation_intent=request.evaluation_intent,
        verifier_graph=verifier_graph,
        required_evidence=required_evidence,
        aggregation_policy=request.aggregation_policy,
        policy_revision=request.policy_revision,
        input_evidence_digests=tuple(sorted(set(request.input_evidence_digests))),
        comparison_cohort_id=request.comparison_cohort_id,
        budget_estimate=budget,
    )
    timestamp = now or datetime.now(UTC)
    return VerificationPlan(
        plan_id=plan_id,
        revision=revision,
        plan_digest=content_hash(spec),
        spec=spec,
        state=PlanState.DRAFT,
        created_at=timestamp,
        updated_at=timestamp,
    )


class InMemoryControlPlane:
    """Thread-safe reference implementation of the M3 state machine."""

    _PLAN_TRANSITIONS: ClassVar[dict[PlanState, set[PlanState]]] = {
        PlanState.DRAFT: {PlanState.VALIDATED, PlanState.INVALID},
        PlanState.VALIDATED: {PlanState.FROZEN, PlanState.INVALID},
        PlanState.FROZEN: {PlanState.SUPERSEDED, PlanState.INVALID},
        PlanState.SUPERSEDED: set(),
        PlanState.INVALID: set(),
    }

    def __init__(self) -> None:
        self._lock = RLock()
        self._plans: dict[tuple[str, int], VerificationPlan] = {}
        self._runs: dict[str, EvalRunRecord] = {}
        self._tasks: dict[tuple[str, str, str], RunTaskRecord] = {}
        self._attempts: dict[str, AttemptLease] = {}
        self._results: dict[tuple[str, str, str, str], FinalResultRecord] = {}
        self._commands: dict[str, CommandReceipt] = {}

    def register_plan(self, plan: VerificationPlan) -> VerificationPlan:
        with self._lock:
            key = (plan.plan_id, plan.revision)
            existing = self._plans.get(key)
            if existing is not None and existing != plan:
                raise IdempotencyConflictError("plan ID/revision already has different content")
            self._plans[key] = plan
            return plan

    def get_plan(self, plan_id: str, revision: int) -> VerificationPlan:
        try:
            return self._plans[(plan_id, revision)]
        except KeyError as exc:
            raise ControlPlaneError(f"unknown plan {plan_id!r} revision {revision}") from exc

    def transition_plan(
        self,
        plan_id: str,
        revision: int,
        target: PlanState,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> VerificationPlan:
        with self._lock:
            current = self.get_plan(plan_id, revision)
            if target not in self._PLAN_TRANSITIONS[current.state]:
                raise InvalidTransitionError(f"cannot transition plan {current.state} -> {target}")
            updated = current.model_copy(
                update={
                    "state": target,
                    "state_reason": reason,
                    "updated_at": now or datetime.now(UTC),
                }
            )
            self._plans[(plan_id, revision)] = updated
            return updated

    def create_run(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        requested_cohort_id: str,
        plan_id: str,
        plan_revision: int,
        plan_digest: str,
        tasks: tuple[RunTaskInput, ...],
        source_run_id: str | None = None,
        now: datetime | None = None,
    ) -> EvalRunRecord:
        timestamp = now or datetime.now(UTC)
        intent = {
            "run_id": run_id,
            "requested_cohort_id": requested_cohort_id,
            "plan_id": plan_id,
            "plan_revision": plan_revision,
            "plan_digest": plan_digest,
            "tasks": tasks,
            "source_run_id": source_run_id,
        }
        intent_digest = content_hash(intent)
        with self._lock:
            receipt = self._commands.get(idempotency_key)
            if receipt is not None:
                if receipt.command_name != "create_run" or receipt.intent_digest != intent_digest:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for different intent"
                    )
                return self._runs[receipt.resource_id]
            if run_id in self._runs:
                raise IdempotencyConflictError("run_id already exists under another command")
            if not tasks:
                raise ControlPlaneError("a run requires at least one task")
            if len({(task.task_id, task.candidate_id) for task in tasks}) != len(tasks):
                raise ControlPlaneError("run tasks must have unique task/candidate identities")
            plan = self.get_plan(plan_id, plan_revision)
            if plan.state is not PlanState.FROZEN:
                raise PlanNotSchedulableError("only frozen plans can be scheduled")
            if plan.plan_digest != plan_digest:
                raise PlanNotSchedulableError("requested plan digest does not match registry")
            if plan.spec.comparison_cohort_id != requested_cohort_id:
                raise PlanNotSchedulableError("requested cohort does not match the frozen plan")

            cohort_id = requested_cohort_id
            related = [
                run for run in self._runs.values() if run.requested_cohort_id == requested_cohort_id
            ]
            same_plan = [run for run in related if run.verification_plan_digest == plan.plan_digest]
            if same_plan:
                cohort_id = same_plan[0].cohort_id
            elif related:
                cohort_id = f"{requested_cohort_id}~{plan.plan_digest[:12]}"

            run = EvalRunRecord(
                run_id=run_id,
                requested_cohort_id=requested_cohort_id,
                cohort_id=cohort_id,
                plan_id=plan_id,
                plan_revision=plan_revision,
                verification_plan_digest=plan.plan_digest,
                state=RunState.PENDING,
                source_run_id=source_run_id,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._runs[run_id] = run
            for task in tasks:
                key = (run_id, task.task_id, task.candidate_id)
                self._tasks[key] = RunTaskRecord(
                    run_id=run_id,
                    task_id=task.task_id,
                    candidate_id=task.candidate_id,
                    candidate_hash=task.candidate_hash,
                    verification_plan_id=plan.plan_id,
                    verification_plan_digest=plan.plan_digest,
                    state=RunTaskState.QUEUED,
                )
            self._commands[idempotency_key] = CommandReceipt(
                idempotency_key=idempotency_key,
                command_name="create_run",
                intent_digest=intent_digest,
                resource_id=run_id,
                created_at=timestamp,
            )
            return run

    def inspect_run(self, run_id: str) -> tuple[EvalRunRecord, tuple[RunTaskRecord, ...]]:
        with self._lock:
            try:
                run = self._runs[run_id]
            except KeyError as exc:
                raise ControlPlaneError(f"unknown run {run_id!r}") from exc
            tasks = tuple(
                sorted(
                    (task for task in self._tasks.values() if task.run_id == run_id),
                    key=lambda task: (task.task_id, task.candidate_id),
                )
            )
            return run, tasks

    def _transition_run_command(
        self,
        *,
        command_name: str,
        idempotency_key: str,
        run_id: str,
        now: datetime,
    ) -> EvalRunRecord:
        intent_digest = content_hash({"command": command_name, "run_id": run_id})
        receipt = self._commands.get(idempotency_key)
        if receipt is not None:
            if receipt.command_name != command_name or receipt.intent_digest != intent_digest:
                raise IdempotencyConflictError(
                    "idempotency key was already used for different intent"
                )
            return self._runs[receipt.resource_id]
        run, tasks = self.inspect_run(run_id)
        if command_name == "cancel_run":
            if run.state is RunState.COMPLETED:
                raise InvalidTransitionError("a completed run cannot be cancelled")
            target = RunState.CANCELLED
            for task in tasks:
                if task.state is RunTaskState.COMPLETED:
                    continue
                self._tasks[(task.run_id, task.task_id, task.candidate_id)] = task.model_copy(
                    update={"state": RunTaskState.CANCELLED}
                )
                if task.current_attempt_id is not None:
                    attempt = self._attempts[task.current_attempt_id]
                    self._attempts[attempt.attempt_id] = attempt.model_copy(
                        update={"state": LeaseState.CANCELLED}
                    )
        else:
            if run.state is not RunState.CANCELLED:
                raise InvalidTransitionError("only a cancelled run can be resumed")
            target = RunState.PENDING
            for task in tasks:
                if task.state is RunTaskState.COMPLETED:
                    continue
                self._tasks[(task.run_id, task.task_id, task.candidate_id)] = task.model_copy(
                    update={"state": RunTaskState.QUEUED, "current_attempt_id": None}
                )
        updated = run.model_copy(update={"state": target, "updated_at": now})
        self._runs[run_id] = updated
        self._commands[idempotency_key] = CommandReceipt(
            idempotency_key=idempotency_key,
            command_name=command_name,
            intent_digest=intent_digest,
            resource_id=run_id,
            created_at=now,
        )
        return updated

    def cancel_run(
        self, run_id: str, *, idempotency_key: str, now: datetime | None = None
    ) -> EvalRunRecord:
        with self._lock:
            return self._transition_run_command(
                command_name="cancel_run",
                idempotency_key=idempotency_key,
                run_id=run_id,
                now=now or datetime.now(UTC),
            )

    def resume_run(
        self, run_id: str, *, idempotency_key: str, now: datetime | None = None
    ) -> EvalRunRecord:
        with self._lock:
            return self._transition_run_command(
                command_name="resume_run",
                idempotency_key=idempotency_key,
                run_id=run_id,
                now=now or datetime.now(UTC),
            )

    def replay_run(
        self,
        source_run_id: str,
        *,
        replay_run_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> EvalRunRecord:
        source, tasks = self.inspect_run(source_run_id)
        plan = next(
            (
                plan
                for plan in self._plans.values()
                if plan.plan_id == source.plan_id
                and plan.revision == source.plan_revision
                and plan.plan_digest == source.verification_plan_digest
            ),
            None,
        )
        if plan is None:
            raise ControlPlaneError("source run references an unavailable verification plan")
        return self.create_run(
            idempotency_key=idempotency_key,
            run_id=replay_run_id,
            requested_cohort_id=source.requested_cohort_id,
            plan_id=plan.plan_id,
            plan_revision=plan.revision,
            plan_digest=plan.plan_digest,
            tasks=tuple(
                RunTaskInput(
                    task_id=task.task_id,
                    candidate_id=task.candidate_id,
                    candidate_hash=task.candidate_hash,
                )
                for task in tasks
            ),
            source_run_id=source_run_id,
            now=now,
        )

    def claim_task(
        self,
        *,
        run_id: str,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> AttemptLease | None:
        timestamp = now or datetime.now(UTC)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._lock:
            run, tasks = self.inspect_run(run_id)
            if run.state not in {RunState.PENDING, RunState.RUNNING}:
                raise InvalidTransitionError("run is not claimable")
            plan = self.get_plan(run.plan_id, run.plan_revision)
            if plan.state is not PlanState.FROZEN:
                raise PlanNotSchedulableError("run plan is no longer frozen")
            queued = [task for task in tasks if task.state is RunTaskState.QUEUED]
            if not queued:
                return None
            if attempt_id in self._attempts:
                raise IdempotencyConflictError("attempt_id already exists")
            task = queued[0]
            lease = AttemptLease(
                attempt_id=attempt_id,
                run_id=run_id,
                task_id=task.task_id,
                candidate_id=task.candidate_id,
                verification_plan_id=task.verification_plan_id,
                verification_plan_digest=task.verification_plan_digest,
                worker_id=worker_id,
                lease_token=lease_token,
                state=LeaseState.ACTIVE,
                claimed_at=timestamp,
                heartbeat_at=timestamp,
                expires_at=timestamp + timedelta(seconds=lease_seconds),
            )
            self._attempts[attempt_id] = lease
            key = (run_id, task.task_id, task.candidate_id)
            self._tasks[key] = task.model_copy(
                update={"state": RunTaskState.LEASED, "current_attempt_id": attempt_id}
            )
            self._runs[run_id] = run.model_copy(
                update={"state": RunState.RUNNING, "updated_at": timestamp}
            )
            return lease

    def heartbeat(
        self,
        attempt_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> AttemptLease:
        timestamp = now or datetime.now(UTC)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._lock:
            try:
                current = self._attempts[attempt_id]
            except KeyError as exc:
                raise LeaseConflictError("unknown attempt") from exc
            if (
                current.state is not LeaseState.ACTIVE
                or current.worker_id != worker_id
                or current.lease_token != lease_token
            ):
                raise LeaseConflictError("worker does not own the active lease")
            if current.expires_at <= timestamp:
                raise LateAttemptError("lease has expired")
            updated = current.model_copy(
                update={
                    "heartbeat_at": timestamp,
                    "expires_at": timestamp + timedelta(seconds=lease_seconds),
                }
            )
            self._attempts[attempt_id] = updated
            return updated

    def reclaim_expired(self, *, now: datetime | None = None) -> int:
        timestamp = now or datetime.now(UTC)
        reclaimed = 0
        with self._lock:
            for attempt_id, attempt in tuple(self._attempts.items()):
                if attempt.state is not LeaseState.ACTIVE or attempt.expires_at > timestamp:
                    continue
                self._attempts[attempt_id] = attempt.model_copy(
                    update={"state": LeaseState.EXPIRED}
                )
                key = (attempt.run_id, attempt.task_id, attempt.candidate_id)
                task = self._tasks[key]
                run = self._runs[attempt.run_id]
                if (
                    task.current_attempt_id == attempt_id
                    and task.state is RunTaskState.LEASED
                    and run.state in {RunState.PENDING, RunState.RUNNING}
                ):
                    self._tasks[key] = task.model_copy(
                        update={"state": RunTaskState.QUEUED, "current_attempt_id": None}
                    )
                    reclaimed += 1
            return reclaimed

    def commit_result(
        self,
        attempt_id: str,
        *,
        worker_id: str,
        lease_token: str,
        result_digest: str,
        result_payload: dict[str, object],
        failure_domain: FailureDomain | None = None,
        now: datetime | None = None,
    ) -> CommitOutcome:
        timestamp = now or datetime.now(UTC)
        with self._lock:
            try:
                attempt = self._attempts[attempt_id]
            except KeyError as exc:
                raise LeaseConflictError("unknown attempt") from exc
            key = (
                attempt.run_id,
                attempt.task_id,
                attempt.candidate_id,
                attempt.verification_plan_digest,
            )
            existing = self._results.get(key)
            if attempt.state is LeaseState.COMPLETED and existing is not None:
                if (
                    existing.attempt_id == attempt_id
                    and existing.result_digest == result_digest
                    and existing.result_payload == result_payload
                    and existing.failure_domain == failure_domain
                ):
                    return CommitOutcome(result=existing, inserted=False)
                raise FinalResultConflictError("task already has a different final result")
            task_key = (attempt.run_id, attempt.task_id, attempt.candidate_id)
            task = self._tasks[task_key]
            if attempt.state is not LeaseState.ACTIVE or task.current_attempt_id != attempt_id:
                raise LateAttemptError("attempt is no longer authoritative")
            if attempt.worker_id != worker_id or attempt.lease_token != lease_token:
                raise LeaseConflictError("worker does not own the active lease")
            if attempt.expires_at <= timestamp:
                raise LateAttemptError("lease expired before result commit")
            if existing is not None:
                raise FinalResultConflictError("task already has an authoritative result")

            result = FinalResultRecord(
                run_id=attempt.run_id,
                task_id=attempt.task_id,
                candidate_id=attempt.candidate_id,
                verification_plan_id=attempt.verification_plan_id,
                verification_plan_digest=attempt.verification_plan_digest,
                attempt_id=attempt_id,
                result_digest=result_digest,
                result_payload=result_payload,
                failure_domain=failure_domain,
                committed_at=timestamp,
            )
            self._results[key] = result
            self._attempts[attempt_id] = attempt.model_copy(update={"state": LeaseState.COMPLETED})
            self._tasks[task_key] = task.model_copy(update={"state": RunTaskState.COMPLETED})
            run, tasks = self.inspect_run(attempt.run_id)
            if all(item.state is RunTaskState.COMPLETED for item in tasks):
                self._runs[run.run_id] = run.model_copy(
                    update={"state": RunState.COMPLETED, "updated_at": timestamp}
                )
            return CommitOutcome(result=result, inserted=True)

    def get_attempt(self, attempt_id: str) -> AttemptLease:
        try:
            return self._attempts[attempt_id]
        except KeyError as exc:
            raise LeaseConflictError("unknown attempt") from exc

    def list_results(self, run_id: str) -> tuple[FinalResultRecord, ...]:
        return tuple(
            sorted(
                (result for result in self._results.values() if result.run_id == run_id),
                key=lambda result: (result.task_id, result.candidate_id),
            )
        )

    def assert_aggregateable(self, run_ids: tuple[str, ...]) -> str:
        with self._lock:
            if not run_ids:
                raise MixedCohortError("aggregation requires at least one run")
            runs = [self.inspect_run(run_id)[0] for run_id in run_ids]
            digests = {run.verification_plan_digest for run in runs}
            cohorts = {run.cohort_id for run in runs}
            if len(digests) != 1 or len(cohorts) != 1:
                raise MixedCohortError("runs do not share one frozen plan and effective cohort")
            return next(iter(digests))
