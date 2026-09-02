"""PostgreSQL implementation of VeriRun's durable M3 control plane."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, ClassVar

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from verirun.canonical import content_hash
from verirun.control_plane import (
    ArtifactMetadataRecord,
    AttemptLease,
    CommandReceipt,
    CommitOutcome,
    ControlPlaneError,
    EvalRunRecord,
    FailureDomain,
    FinalResultConflictError,
    FinalResultRecord,
    IdempotencyConflictError,
    InvalidTransitionError,
    LateAttemptError,
    LeaseConflictError,
    LeaseState,
    MixedCohortError,
    PlanNotSchedulableError,
    PlanState,
    RunState,
    RunTaskInput,
    RunTaskRecord,
    RunTaskState,
    VerificationPlan,
)

MIGRATION_VERSION = 2

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS verirun_schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS verification_plans (
    plan_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    plan_digest char(64) NOT NULL CHECK (plan_digest ~ '^[0-9a-f]{64}$'),
    spec jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('draft', 'validated', 'frozen', 'superseded', 'invalid')),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    state_reason text,
    PRIMARY KEY (plan_id, revision),
    UNIQUE (plan_id, revision, plan_digest)
);

CREATE TABLE IF NOT EXISTS comparison_cohorts (
    requested_cohort_id text NOT NULL,
    plan_digest char(64) NOT NULL CHECK (plan_digest ~ '^[0-9a-f]{64}$'),
    cohort_id text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (requested_cohort_id, plan_digest)
);

CREATE TABLE IF NOT EXISTS eval_runs (
    run_id text PRIMARY KEY,
    requested_cohort_id text NOT NULL,
    cohort_id text NOT NULL,
    plan_id text NOT NULL,
    plan_revision integer NOT NULL,
    verification_plan_digest char(64) NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'running', 'cancelled', 'completed')),
    source_run_id text REFERENCES eval_runs(run_id),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    FOREIGN KEY (plan_id, plan_revision, verification_plan_digest)
        REFERENCES verification_plans(plan_id, revision, plan_digest)
);

CREATE TABLE IF NOT EXISTS run_tasks (
    run_id text NOT NULL REFERENCES eval_runs(run_id),
    task_id text NOT NULL,
    candidate_id text NOT NULL,
    candidate_hash char(64) NOT NULL CHECK (candidate_hash ~ '^[0-9a-f]{64}$'),
    verification_plan_id text NOT NULL,
    verification_plan_digest char(64) NOT NULL,
    state text NOT NULL CHECK (state IN ('queued', 'leased', 'cancelled', 'completed')),
    current_attempt_id text,
    PRIMARY KEY (run_id, task_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS run_tasks_claim_idx
    ON run_tasks (run_id, state, task_id, candidate_id);

CREATE TABLE IF NOT EXISTS attempt_leases (
    attempt_id text PRIMARY KEY,
    run_id text NOT NULL,
    task_id text NOT NULL,
    candidate_id text NOT NULL,
    verification_plan_id text NOT NULL,
    verification_plan_digest char(64) NOT NULL,
    worker_id text NOT NULL,
    lease_token text NOT NULL,
    state text NOT NULL CHECK (state IN ('active', 'expired', 'cancelled', 'completed')),
    claimed_at timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    FOREIGN KEY (run_id, task_id, candidate_id)
        REFERENCES run_tasks(run_id, task_id, candidate_id)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'run_tasks_current_attempt_fk'
          AND conrelid = 'run_tasks'::regclass
    ) THEN
        ALTER TABLE run_tasks
            ADD CONSTRAINT run_tasks_current_attempt_fk
            FOREIGN KEY (current_attempt_id) REFERENCES attempt_leases(attempt_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS attempt_leases_expiry_idx
    ON attempt_leases (state, expires_at);

CREATE TABLE IF NOT EXISTS final_results (
    run_id text NOT NULL,
    task_id text NOT NULL,
    candidate_id text NOT NULL,
    verification_plan_id text NOT NULL,
    verification_plan_digest char(64) NOT NULL,
    attempt_id text NOT NULL UNIQUE REFERENCES attempt_leases(attempt_id),
    result_digest char(64) NOT NULL CHECK (result_digest ~ '^[0-9a-f]{64}$'),
    result_payload jsonb NOT NULL,
    failure_domain text CHECK (
        failure_domain IS NULL OR failure_domain IN (
            'plan', 'model', 'user_code', 'verifier', 'sandbox', 'scheduler', 'storage'
        )
    ),
    committed_at timestamptz NOT NULL,
    PRIMARY KEY (run_id, task_id, candidate_id, verification_plan_digest),
    FOREIGN KEY (run_id, task_id, candidate_id)
        REFERENCES run_tasks(run_id, task_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS command_receipts (
    idempotency_key text PRIMARY KEY,
    command_name text NOT NULL,
    intent_digest char(64) NOT NULL CHECK (intent_digest ~ '^[0-9a-f]{64}$'),
    resource_id text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_metadata (
    sha256 char(64) PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    kind text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    media_type text NOT NULL,
    storage_uri text NOT NULL,
    created_at timestamptz NOT NULL
);
"""

MIGRATION_2_SQL = """
ALTER TABLE run_tasks ADD COLUMN IF NOT EXISTS verification_plan_id text;
UPDATE run_tasks t
SET verification_plan_id = r.plan_id
FROM eval_runs r
WHERE t.run_id = r.run_id AND t.verification_plan_id IS NULL;
ALTER TABLE run_tasks ALTER COLUMN verification_plan_id SET NOT NULL;

ALTER TABLE attempt_leases ADD COLUMN IF NOT EXISTS verification_plan_id text;
UPDATE attempt_leases a
SET verification_plan_id = r.plan_id
FROM eval_runs r
WHERE a.run_id = r.run_id AND a.verification_plan_id IS NULL;
ALTER TABLE attempt_leases ALTER COLUMN verification_plan_id SET NOT NULL;

ALTER TABLE final_results ADD COLUMN IF NOT EXISTS verification_plan_id text;
UPDATE final_results f
SET verification_plan_id = r.plan_id
FROM eval_runs r
WHERE f.run_id = r.run_id AND f.verification_plan_id IS NULL;
ALTER TABLE final_results ALTER COLUMN verification_plan_id SET NOT NULL;
"""


class PostgresControlPlane:
    """Transactional control-plane API backed by PostgreSQL."""

    _PLAN_TRANSITIONS: ClassVar[dict[PlanState, set[PlanState]]] = {
        PlanState.DRAFT: {PlanState.VALIDATED, PlanState.INVALID},
        PlanState.VALIDATED: {PlanState.FROZEN, PlanState.INVALID},
        PlanState.FROZEN: {PlanState.SUPERSEDED, PlanState.INVALID},
        PlanState.SUPERSEDED: set(),
        PlanState.INVALID: set(),
    }

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN cannot be empty")
        self.dsn = dsn

    @contextmanager
    def _connection(self) -> Iterator[Connection[dict[str, Any]]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            yield connection

    def migrate(self) -> None:
        with self._connection() as connection:
            connection.execute(MIGRATION_SQL)
            connection.execute(
                """INSERT INTO verirun_schema_migrations (version)
                   VALUES (%s) ON CONFLICT (version) DO NOTHING""",
                (1,),
            )
            connection.execute(MIGRATION_2_SQL)
            connection.execute(
                """INSERT INTO verirun_schema_migrations (version)
                   VALUES (%s) ON CONFLICT (version) DO NOTHING""",
                (MIGRATION_VERSION,),
            )

    @staticmethod
    def _timestamp(connection: Connection[dict[str, Any]], supplied: datetime | None) -> datetime:
        if supplied is not None:
            return supplied
        row = connection.execute("SELECT clock_timestamp() AS current_time").fetchone()
        assert row is not None
        return datetime.fromisoformat(str(row["current_time"]))

    @staticmethod
    def _plan(row: dict[str, Any]) -> VerificationPlan:
        return VerificationPlan.model_validate(
            {
                "plan_id": row["plan_id"],
                "revision": row["revision"],
                "plan_digest": row["plan_digest"],
                "spec": row["spec"],
                "state": row["state"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "state_reason": row["state_reason"],
            }
        )

    @staticmethod
    def _run(row: dict[str, Any]) -> EvalRunRecord:
        return EvalRunRecord.model_validate(row)

    @staticmethod
    def _task(row: dict[str, Any]) -> RunTaskRecord:
        return RunTaskRecord.model_validate(row)

    @staticmethod
    def _attempt(row: dict[str, Any]) -> AttemptLease:
        return AttemptLease.model_validate(row)

    @staticmethod
    def _result(row: dict[str, Any]) -> FinalResultRecord:
        return FinalResultRecord.model_validate(row)

    @staticmethod
    def _receipt(row: dict[str, Any]) -> CommandReceipt:
        return CommandReceipt.model_validate(row)

    def register_plan(self, plan: VerificationPlan) -> VerificationPlan:
        with self._connection() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"plan:{plan.plan_id}:{plan.revision}",),
            )
            row = connection.execute(
                """SELECT * FROM verification_plans
                   WHERE plan_id = %s AND revision = %s FOR UPDATE""",
                (plan.plan_id, plan.revision),
            ).fetchone()
            if row is not None:
                existing = self._plan(row)
                if existing != plan:
                    raise IdempotencyConflictError("plan ID/revision already has different content")
                return existing
            connection.execute(
                """INSERT INTO verification_plans (
                       plan_id, revision, plan_digest, spec, state,
                       created_at, updated_at, state_reason
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    plan.plan_id,
                    plan.revision,
                    plan.plan_digest,
                    Jsonb(plan.spec.model_dump(mode="json")),
                    plan.state.value,
                    plan.created_at,
                    plan.updated_at,
                    plan.state_reason,
                ),
            )
            return plan

    def get_plan(self, plan_id: str, revision: int) -> VerificationPlan:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM verification_plans WHERE plan_id = %s AND revision = %s",
                (plan_id, revision),
            ).fetchone()
        if row is None:
            raise ControlPlaneError(f"unknown plan {plan_id!r} revision {revision}")
        return self._plan(row)

    def transition_plan(
        self,
        plan_id: str,
        revision: int,
        target: PlanState,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> VerificationPlan:
        with self._connection() as connection:
            timestamp = self._timestamp(connection, now)
            row = connection.execute(
                """SELECT * FROM verification_plans
                   WHERE plan_id = %s AND revision = %s FOR UPDATE""",
                (plan_id, revision),
            ).fetchone()
            if row is None:
                raise ControlPlaneError(f"unknown plan {plan_id!r} revision {revision}")
            current = self._plan(row)
            if target not in self._PLAN_TRANSITIONS[current.state]:
                raise InvalidTransitionError(f"cannot transition plan {current.state} -> {target}")
            row = connection.execute(
                """UPDATE verification_plans
                   SET state = %s, state_reason = %s, updated_at = %s
                   WHERE plan_id = %s AND revision = %s RETURNING *""",
                (target.value, reason, timestamp, plan_id, revision),
            ).fetchone()
            assert row is not None
            return self._plan(row)

    @staticmethod
    def _command_receipt(
        connection: Connection[dict[str, Any]], idempotency_key: str
    ) -> CommandReceipt | None:
        row = connection.execute(
            "SELECT * FROM command_receipts WHERE idempotency_key = %s FOR UPDATE",
            (idempotency_key,),
        ).fetchone()
        return None if row is None else PostgresControlPlane._receipt(row)

    @staticmethod
    def _insert_receipt(
        connection: Connection[dict[str, Any]],
        *,
        idempotency_key: str,
        command_name: str,
        intent_digest: str,
        resource_id: str,
        timestamp: datetime,
    ) -> None:
        connection.execute(
            """INSERT INTO command_receipts (
                   idempotency_key, command_name, intent_digest, resource_id, created_at
               ) VALUES (%s, %s, %s, %s, %s)""",
            (idempotency_key, command_name, intent_digest, resource_id, timestamp),
        )

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
        intent_digest = content_hash(
            {
                "run_id": run_id,
                "requested_cohort_id": requested_cohort_id,
                "plan_id": plan_id,
                "plan_revision": plan_revision,
                "plan_digest": plan_digest,
                "tasks": tasks,
                "source_run_id": source_run_id,
            }
        )
        if not tasks:
            raise ControlPlaneError("a run requires at least one task")
        if len({(task.task_id, task.candidate_id) for task in tasks}) != len(tasks):
            raise ControlPlaneError("run tasks must have unique task/candidate identities")

        with self._connection() as connection:
            timestamp = self._timestamp(connection, now)
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"command:{idempotency_key}",),
            )
            receipt = self._command_receipt(connection, idempotency_key)
            if receipt is not None:
                if receipt.command_name != "create_run" or receipt.intent_digest != intent_digest:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for different intent"
                    )
                row = connection.execute(
                    "SELECT * FROM eval_runs WHERE run_id = %s", (receipt.resource_id,)
                ).fetchone()
                assert row is not None
                return self._run(row)
            if connection.execute(
                "SELECT 1 FROM eval_runs WHERE run_id = %s", (run_id,)
            ).fetchone():
                raise IdempotencyConflictError("run_id already exists under another command")
            plan_row = connection.execute(
                """SELECT * FROM verification_plans
                   WHERE plan_id = %s AND revision = %s FOR SHARE""",
                (plan_id, plan_revision),
            ).fetchone()
            if plan_row is None:
                raise ControlPlaneError(f"unknown plan {plan_id!r} revision {plan_revision}")
            plan = self._plan(plan_row)
            if plan.state is not PlanState.FROZEN:
                raise PlanNotSchedulableError("only frozen plans can be scheduled")
            if plan.plan_digest != plan_digest:
                raise PlanNotSchedulableError("requested plan digest does not match registry")
            if plan.spec.comparison_cohort_id != requested_cohort_id:
                raise PlanNotSchedulableError("requested cohort does not match the frozen plan")

            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (requested_cohort_id,),
            )
            cohort_row = connection.execute(
                """SELECT cohort_id FROM comparison_cohorts
                   WHERE requested_cohort_id = %s AND plan_digest = %s""",
                (requested_cohort_id, plan_digest),
            ).fetchone()
            if cohort_row is None:
                has_related = connection.execute(
                    """SELECT 1 FROM comparison_cohorts
                       WHERE requested_cohort_id = %s LIMIT 1""",
                    (requested_cohort_id,),
                ).fetchone()
                cohort_id = (
                    f"{requested_cohort_id}~{plan_digest[:12]}"
                    if has_related
                    else requested_cohort_id
                )
                connection.execute(
                    """INSERT INTO comparison_cohorts (
                           requested_cohort_id, plan_digest, cohort_id, created_at
                       ) VALUES (%s, %s, %s, %s)""",
                    (requested_cohort_id, plan_digest, cohort_id, timestamp),
                )
            else:
                cohort_id = str(cohort_row["cohort_id"])

            row = connection.execute(
                """INSERT INTO eval_runs (
                       run_id, requested_cohort_id, cohort_id, plan_id, plan_revision,
                       verification_plan_digest, state, source_run_id, created_at, updated_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    run_id,
                    requested_cohort_id,
                    cohort_id,
                    plan_id,
                    plan_revision,
                    plan_digest,
                    RunState.PENDING.value,
                    source_run_id,
                    timestamp,
                    timestamp,
                ),
            ).fetchone()
            assert row is not None
            for task in tasks:
                connection.execute(
                    """INSERT INTO run_tasks (
                           run_id, task_id, candidate_id, candidate_hash,
                           verification_plan_id, verification_plan_digest, state
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        run_id,
                        task.task_id,
                        task.candidate_id,
                        task.candidate_hash,
                        plan_id,
                        plan_digest,
                        RunTaskState.QUEUED.value,
                    ),
                )
            self._insert_receipt(
                connection,
                idempotency_key=idempotency_key,
                command_name="create_run",
                intent_digest=intent_digest,
                resource_id=run_id,
                timestamp=timestamp,
            )
            return self._run(row)

    def inspect_run(self, run_id: str) -> tuple[EvalRunRecord, tuple[RunTaskRecord, ...]]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM eval_runs WHERE run_id = %s", (run_id,)
            ).fetchone()
            if row is None:
                raise ControlPlaneError(f"unknown run {run_id!r}")
            task_rows = connection.execute(
                """SELECT * FROM run_tasks WHERE run_id = %s
                   ORDER BY task_id, candidate_id""",
                (run_id,),
            ).fetchall()
        return self._run(row), tuple(self._task(task_row) for task_row in task_rows)

    def _transition_run_command(
        self,
        *,
        command_name: str,
        idempotency_key: str,
        run_id: str,
        now: datetime | None,
    ) -> EvalRunRecord:
        intent_digest = content_hash({"command": command_name, "run_id": run_id})
        with self._connection() as connection:
            timestamp = self._timestamp(connection, now)
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"command:{idempotency_key}",),
            )
            receipt = self._command_receipt(connection, idempotency_key)
            if receipt is not None:
                if receipt.command_name != command_name or receipt.intent_digest != intent_digest:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for different intent"
                    )
                row = connection.execute(
                    "SELECT * FROM eval_runs WHERE run_id = %s", (receipt.resource_id,)
                ).fetchone()
                assert row is not None
                return self._run(row)
            row = connection.execute(
                "SELECT * FROM eval_runs WHERE run_id = %s FOR UPDATE", (run_id,)
            ).fetchone()
            if row is None:
                raise ControlPlaneError(f"unknown run {run_id!r}")
            run = self._run(row)
            if command_name == "cancel_run":
                if run.state is RunState.COMPLETED:
                    raise InvalidTransitionError("a completed run cannot be cancelled")
                connection.execute(
                    """UPDATE attempt_leases SET state = %s
                       WHERE run_id = %s AND state = %s""",
                    (LeaseState.CANCELLED.value, run_id, LeaseState.ACTIVE.value),
                )
                connection.execute(
                    """UPDATE run_tasks SET state = %s
                       WHERE run_id = %s AND state <> %s""",
                    (RunTaskState.CANCELLED.value, run_id, RunTaskState.COMPLETED.value),
                )
                target = RunState.CANCELLED
            else:
                if run.state is not RunState.CANCELLED:
                    raise InvalidTransitionError("only a cancelled run can be resumed")
                connection.execute(
                    """UPDATE run_tasks SET state = %s, current_attempt_id = NULL
                       WHERE run_id = %s AND state <> %s""",
                    (RunTaskState.QUEUED.value, run_id, RunTaskState.COMPLETED.value),
                )
                target = RunState.PENDING
            row = connection.execute(
                """UPDATE eval_runs SET state = %s, updated_at = %s
                   WHERE run_id = %s RETURNING *""",
                (target.value, timestamp, run_id),
            ).fetchone()
            assert row is not None
            self._insert_receipt(
                connection,
                idempotency_key=idempotency_key,
                command_name=command_name,
                intent_digest=intent_digest,
                resource_id=run_id,
                timestamp=timestamp,
            )
            return self._run(row)

    def cancel_run(
        self, run_id: str, *, idempotency_key: str, now: datetime | None = None
    ) -> EvalRunRecord:
        return self._transition_run_command(
            command_name="cancel_run",
            idempotency_key=idempotency_key,
            run_id=run_id,
            now=now,
        )

    def resume_run(
        self, run_id: str, *, idempotency_key: str, now: datetime | None = None
    ) -> EvalRunRecord:
        return self._transition_run_command(
            command_name="resume_run",
            idempotency_key=idempotency_key,
            run_id=run_id,
            now=now,
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
        return self.create_run(
            idempotency_key=idempotency_key,
            run_id=replay_run_id,
            requested_cohort_id=source.requested_cohort_id,
            plan_id=source.plan_id,
            plan_revision=source.plan_revision,
            plan_digest=source.verification_plan_digest,
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
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._connection() as connection:
            timestamp = self._timestamp(connection, now)
            run_row = connection.execute(
                """SELECT r.*, p.state AS plan_state
                   FROM eval_runs r
                   JOIN verification_plans p
                     ON p.plan_id = r.plan_id AND p.revision = r.plan_revision
                   WHERE r.run_id = %s FOR UPDATE OF r, p""",
                (run_id,),
            ).fetchone()
            if run_row is None:
                raise ControlPlaneError(f"unknown run {run_id!r}")
            run = self._run({key: value for key, value in run_row.items() if key != "plan_state"})
            if run.state not in {RunState.PENDING, RunState.RUNNING}:
                raise InvalidTransitionError("run is not claimable")
            if run_row["plan_state"] != PlanState.FROZEN.value:
                raise PlanNotSchedulableError("run plan is no longer frozen")
            if connection.execute(
                "SELECT 1 FROM attempt_leases WHERE attempt_id = %s", (attempt_id,)
            ).fetchone():
                raise IdempotencyConflictError("attempt_id already exists")
            task_row = connection.execute(
                """SELECT * FROM run_tasks
                   WHERE run_id = %s AND state = %s
                   ORDER BY task_id, candidate_id
                   FOR UPDATE SKIP LOCKED LIMIT 1""",
                (run_id, RunTaskState.QUEUED.value),
            ).fetchone()
            if task_row is None:
                return None
            task = self._task(task_row)
            expires_at = timestamp + timedelta(seconds=lease_seconds)
            row = connection.execute(
                """INSERT INTO attempt_leases (
                       attempt_id, run_id, task_id, candidate_id, verification_plan_id,
                       verification_plan_digest, worker_id, lease_token, state,
                       claimed_at, heartbeat_at, expires_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    attempt_id,
                    run_id,
                    task.task_id,
                    task.candidate_id,
                    task.verification_plan_id,
                    task.verification_plan_digest,
                    worker_id,
                    lease_token,
                    LeaseState.ACTIVE.value,
                    timestamp,
                    timestamp,
                    expires_at,
                ),
            ).fetchone()
            connection.execute(
                """UPDATE run_tasks SET state = %s, current_attempt_id = %s
                   WHERE run_id = %s AND task_id = %s AND candidate_id = %s""",
                (
                    RunTaskState.LEASED.value,
                    attempt_id,
                    run_id,
                    task.task_id,
                    task.candidate_id,
                ),
            )
            connection.execute(
                "UPDATE eval_runs SET state = %s, updated_at = %s WHERE run_id = %s",
                (RunState.RUNNING.value, timestamp, run_id),
            )
            assert row is not None
            return self._attempt(row)

    def heartbeat(
        self,
        attempt_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> AttemptLease:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._connection() as connection:
            timestamp = self._timestamp(connection, now)
            row = connection.execute(
                "SELECT * FROM attempt_leases WHERE attempt_id = %s FOR UPDATE",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise LeaseConflictError("unknown attempt")
            attempt = self._attempt(row)
            if (
                attempt.state is not LeaseState.ACTIVE
                or attempt.worker_id != worker_id
                or attempt.lease_token != lease_token
            ):
                raise LeaseConflictError("worker does not own the active lease")
            if attempt.expires_at <= timestamp:
                raise LateAttemptError("lease has expired")
            row = connection.execute(
                """UPDATE attempt_leases SET heartbeat_at = %s, expires_at = %s
                   WHERE attempt_id = %s RETURNING *""",
                (timestamp, timestamp + timedelta(seconds=lease_seconds), attempt_id),
            ).fetchone()
            assert row is not None
            return self._attempt(row)

    def reclaim_expired(self, *, now: datetime | None = None) -> int:
        with self._connection() as connection:
            timestamp = self._timestamp(connection, now)
            rows = connection.execute(
                """SELECT a.* FROM attempt_leases a
                   JOIN eval_runs r ON r.run_id = a.run_id
                   WHERE a.state = %s AND a.expires_at <= %s
                     AND r.state IN (%s, %s)
                   FOR UPDATE OF a SKIP LOCKED""",
                (
                    LeaseState.ACTIVE.value,
                    timestamp,
                    RunState.PENDING.value,
                    RunState.RUNNING.value,
                ),
            ).fetchall()
            reclaimed = 0
            for row in rows:
                attempt = self._attempt(row)
                connection.execute(
                    "UPDATE attempt_leases SET state = %s WHERE attempt_id = %s",
                    (LeaseState.EXPIRED.value, attempt.attempt_id),
                )
                cursor = connection.execute(
                    """UPDATE run_tasks SET state = %s, current_attempt_id = NULL
                       WHERE run_id = %s AND task_id = %s AND candidate_id = %s
                         AND current_attempt_id = %s AND state = %s""",
                    (
                        RunTaskState.QUEUED.value,
                        attempt.run_id,
                        attempt.task_id,
                        attempt.candidate_id,
                        attempt.attempt_id,
                        RunTaskState.LEASED.value,
                    ),
                )
                reclaimed += cursor.rowcount
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
        with self._connection() as connection:
            timestamp = self._timestamp(connection, now)
            row = connection.execute(
                "SELECT * FROM attempt_leases WHERE attempt_id = %s FOR UPDATE",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise LeaseConflictError("unknown attempt")
            attempt = self._attempt(row)
            existing_row = connection.execute(
                """SELECT * FROM final_results
                   WHERE run_id = %s AND task_id = %s AND candidate_id = %s
                     AND verification_plan_digest = %s FOR UPDATE""",
                (
                    attempt.run_id,
                    attempt.task_id,
                    attempt.candidate_id,
                    attempt.verification_plan_digest,
                ),
            ).fetchone()
            if attempt.state is LeaseState.COMPLETED and existing_row is not None:
                existing = self._result(existing_row)
                if (
                    existing.attempt_id == attempt_id
                    and existing.result_digest == result_digest
                    and existing.result_payload == result_payload
                    and existing.failure_domain == failure_domain
                ):
                    return CommitOutcome(result=existing, inserted=False)
                raise FinalResultConflictError("task already has a different final result")
            task_row = connection.execute(
                """SELECT * FROM run_tasks
                   WHERE run_id = %s AND task_id = %s AND candidate_id = %s FOR UPDATE""",
                (attempt.run_id, attempt.task_id, attempt.candidate_id),
            ).fetchone()
            assert task_row is not None
            task = self._task(task_row)
            if attempt.state is not LeaseState.ACTIVE or task.current_attempt_id != attempt_id:
                raise LateAttemptError("attempt is no longer authoritative")
            if attempt.worker_id != worker_id or attempt.lease_token != lease_token:
                raise LeaseConflictError("worker does not own the active lease")
            if attempt.expires_at <= timestamp:
                raise LateAttemptError("lease expired before result commit")
            if existing_row is not None:
                raise FinalResultConflictError("task already has an authoritative result")

            row = connection.execute(
                """INSERT INTO final_results (
                       run_id, task_id, candidate_id, verification_plan_id,
                       verification_plan_digest, attempt_id, result_digest,
                       result_payload, failure_domain, committed_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    attempt.run_id,
                    attempt.task_id,
                    attempt.candidate_id,
                    attempt.verification_plan_id,
                    attempt.verification_plan_digest,
                    attempt_id,
                    result_digest,
                    Jsonb(result_payload),
                    None if failure_domain is None else failure_domain.value,
                    timestamp,
                ),
            ).fetchone()
            connection.execute(
                "UPDATE attempt_leases SET state = %s WHERE attempt_id = %s",
                (LeaseState.COMPLETED.value, attempt_id),
            )
            connection.execute(
                """UPDATE run_tasks SET state = %s
                   WHERE run_id = %s AND task_id = %s AND candidate_id = %s""",
                (
                    RunTaskState.COMPLETED.value,
                    attempt.run_id,
                    attempt.task_id,
                    attempt.candidate_id,
                ),
            )
            unfinished = connection.execute(
                "SELECT 1 FROM run_tasks WHERE run_id = %s AND state <> %s LIMIT 1",
                (attempt.run_id, RunTaskState.COMPLETED.value),
            ).fetchone()
            if unfinished is None:
                connection.execute(
                    "UPDATE eval_runs SET state = %s, updated_at = %s WHERE run_id = %s",
                    (RunState.COMPLETED.value, timestamp, attempt.run_id),
                )
            assert row is not None
            return CommitOutcome(result=self._result(row), inserted=True)

    def get_attempt(self, attempt_id: str) -> AttemptLease:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM attempt_leases WHERE attempt_id = %s", (attempt_id,)
            ).fetchone()
        if row is None:
            raise LeaseConflictError("unknown attempt")
        return self._attempt(row)

    def list_results(self, run_id: str) -> tuple[FinalResultRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM final_results WHERE run_id = %s
                   ORDER BY task_id, candidate_id""",
                (run_id,),
            ).fetchall()
        return tuple(self._result(row) for row in rows)

    def assert_aggregateable(self, run_ids: tuple[str, ...]) -> str:
        if not run_ids:
            raise MixedCohortError("aggregation requires at least one run")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT run_id, cohort_id, verification_plan_digest FROM eval_runs
                   WHERE run_id = ANY(%s)""",
                (list(run_ids),),
            ).fetchall()
        if len(rows) != len(set(run_ids)):
            raise ControlPlaneError("one or more aggregation runs are unknown")
        digests = {str(row["verification_plan_digest"]) for row in rows}
        cohorts = {str(row["cohort_id"]) for row in rows}
        if len(digests) != 1 or len(cohorts) != 1:
            raise MixedCohortError("runs do not share one frozen plan and effective cohort")
        return next(iter(digests))

    def register_artifact(self, artifact: ArtifactMetadataRecord) -> ArtifactMetadataRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifact_metadata WHERE sha256 = %s FOR UPDATE",
                (artifact.sha256,),
            ).fetchone()
            if row is not None:
                existing = ArtifactMetadataRecord.model_validate(row)
                if existing != artifact:
                    raise IdempotencyConflictError("artifact digest already has different metadata")
                return existing
            connection.execute(
                """INSERT INTO artifact_metadata (
                       sha256, kind, size_bytes, media_type, storage_uri, created_at
                   ) VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    artifact.sha256,
                    artifact.kind,
                    artifact.size_bytes,
                    artifact.media_type,
                    artifact.storage_uri,
                    artifact.created_at,
                ),
            )
            return artifact

    def get_artifact(self, digest: str) -> ArtifactMetadataRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifact_metadata WHERE sha256 = %s", (digest,)
            ).fetchone()
        if row is None:
            raise ControlPlaneError(f"unknown artifact {digest}")
        return ArtifactMetadataRecord.model_validate(row)
