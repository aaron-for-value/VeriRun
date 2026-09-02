from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from verirun.control_plane import (
    AggregationPolicy,
    ArtifactMetadataRecord,
    EvaluationIntent,
    FinalResultConflictError,
    IdempotencyConflictError,
    LateAttemptError,
    MixedCohortError,
    PlanCompileRequest,
    PlanState,
    RunState,
    RunTaskInput,
    TaskSpec,
    VerifierCatalogEntry,
    compile_verification_plan,
)
from verirun.control_plane_smoke import run_control_plane_smoke
from verirun.postgres import PostgresControlPlane

DSN = os.environ.get("VERIRUN_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(DSN is None, reason="VERIRUN_TEST_POSTGRES_DSN is unset")
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def request(*, image_digest: str = "b" * 64) -> PlanCompileRequest:
    return PlanCompileRequest(
        task_spec=TaskSpec(task_id="task-1", task_family="python"),
        evaluation_intent=EvaluationIntent(name="correctness"),
        verifier_catalog=(
            VerifierCatalogEntry(
                verifier_id="tests",
                task_families=("python",),
                intents=("correctness",),
                adapter="tests",
                version="1",
                config_digest="a" * 64,
                image_digest=image_digest,
                required_evidence=("stdout", "stderr"),
                expected_sandbox_cpu_seconds=2,
            ),
        ),
        aggregation_policy=AggregationPolicy(
            policy_id="pass-rate",
            version="1",
            reducer="mean",
            config_digest="c" * 64,
        ),
        policy_revision="policy-1",
        comparison_cohort_id="cohort-a",
        candidate_count=1,
        max_concurrency=1,
    )


@pytest.fixture
def plane() -> PostgresControlPlane:
    assert DSN is not None
    backend = PostgresControlPlane(DSN)
    backend.migrate()
    with psycopg.connect(DSN) as connection:
        connection.execute(
            """TRUNCATE artifact_metadata, final_results, attempt_leases, run_tasks,
                      command_receipts, eval_runs, comparison_cohorts, verification_plans
               CASCADE"""
        )
    return backend


def freeze(plane: PostgresControlPlane, *, plan_id: str, image_digest: str = "b" * 64) -> str:
    plan = compile_verification_plan(
        plan_id=plan_id,
        revision=1,
        request=request(image_digest=image_digest),
        now=NOW,
    )
    plane.register_plan(plan)
    plane.transition_plan(plan_id, 1, PlanState.VALIDATED, reason="valid", now=NOW)
    return plane.transition_plan(plan_id, 1, PlanState.FROZEN, reason="frozen", now=NOW).plan_digest


def create(plane: PostgresControlPlane, digest: str, *, run_id: str = "run-a") -> None:
    plane.create_run(
        idempotency_key=f"create-{run_id}",
        run_id=run_id,
        requested_cohort_id="cohort-a",
        plan_id="plan-a",
        plan_revision=1,
        plan_digest=digest,
        tasks=(
            RunTaskInput(task_id="task-1", candidate_id="candidate-1", candidate_hash="d" * 64),
        ),
        now=NOW,
    )


def test_postgres_survives_client_restart_and_worker_takeover(
    plane: PostgresControlPlane,
) -> None:
    digest = freeze(plane, plan_id="plan-a")
    create(plane, digest)
    first = plane.claim_task(
        run_id="run-a",
        worker_id="worker-a",
        attempt_id="attempt-a",
        lease_token="token-a",
        lease_seconds=5,
        now=NOW,
    )
    assert first is not None

    assert DSN is not None
    restarted = PostgresControlPlane(DSN)
    recovered_run, recovered_tasks = restarted.inspect_run("run-a")
    assert recovered_run.verification_plan_digest == digest
    assert recovered_tasks[0].verification_plan_id == "plan-a"
    assert restarted.reclaim_expired(now=NOW + timedelta(seconds=6)) == 1
    second = restarted.claim_task(
        run_id="run-a",
        worker_id="worker-b",
        attempt_id="attempt-b",
        lease_token="token-b",
        lease_seconds=30,
        now=NOW + timedelta(seconds=7),
    )
    assert second is not None
    assert second.verification_plan_id == "plan-a"
    assert second.verification_plan_digest == digest

    with pytest.raises(LateAttemptError, match="no longer authoritative"):
        restarted.commit_result(
            "attempt-a",
            worker_id="worker-a",
            lease_token="token-a",
            result_digest="e" * 64,
            result_payload={"status": "late"},
            now=NOW + timedelta(seconds=8),
        )
    first_commit = restarted.commit_result(
        "attempt-b",
        worker_id="worker-b",
        lease_token="token-b",
        result_digest="f" * 64,
        result_payload={"status": "passed"},
        now=NOW + timedelta(seconds=8),
    )
    repeated = restarted.commit_result(
        "attempt-b",
        worker_id="worker-b",
        lease_token="token-b",
        result_digest="f" * 64,
        result_payload={"status": "passed"},
        now=NOW + timedelta(seconds=9),
    )
    assert first_commit.inserted is True
    assert first_commit.result.verification_plan_id == "plan-a"
    assert repeated.inserted is False
    assert len(restarted.list_results("run-a")) == 1
    assert restarted.inspect_run("run-a")[0].state is RunState.COMPLETED
    with psycopg.connect(DSN) as connection:
        versions = connection.execute(
            "SELECT version FROM verirun_schema_migrations ORDER BY version"
        ).fetchall()
    assert versions == [(1,), (2,)]
    with pytest.raises(FinalResultConflictError, match="different final"):
        restarted.commit_result(
            "attempt-b",
            worker_id="worker-b",
            lease_token="token-b",
            result_digest="0" * 64,
            result_payload={"status": "changed"},
            now=NOW + timedelta(seconds=10),
        )
    with pytest.raises(FinalResultConflictError, match="different final"):
        restarted.commit_result(
            "attempt-b",
            worker_id="worker-b",
            lease_token="token-b",
            result_digest="f" * 64,
            result_payload={"status": "same digest, different payload"},
            now=NOW + timedelta(seconds=11),
        )


def test_postgres_command_idempotency_cohort_split_and_artifact_metadata(
    plane: PostgresControlPlane,
) -> None:
    first_digest = freeze(plane, plan_id="plan-a")
    create(plane, first_digest)
    same = plane.create_run(
        idempotency_key="create-run-a",
        run_id="run-a",
        requested_cohort_id="cohort-a",
        plan_id="plan-a",
        plan_revision=1,
        plan_digest=first_digest,
        tasks=(
            RunTaskInput(task_id="task-1", candidate_id="candidate-1", candidate_hash="d" * 64),
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert same.run_id == "run-a"
    with pytest.raises(IdempotencyConflictError, match="different intent"):
        plane.create_run(
            idempotency_key="create-run-a",
            run_id="run-b",
            requested_cohort_id="cohort-a",
            plan_id="plan-a",
            plan_revision=1,
            plan_digest=first_digest,
            tasks=(
                RunTaskInput(task_id="task-1", candidate_id="candidate-2", candidate_hash="e" * 64),
            ),
            now=NOW,
        )

    second_digest = freeze(plane, plan_id="plan-b", image_digest="9" * 64)
    plane.create_run(
        idempotency_key="create-run-b",
        run_id="run-b",
        requested_cohort_id="cohort-a",
        plan_id="plan-b",
        plan_revision=1,
        plan_digest=second_digest,
        tasks=(
            RunTaskInput(task_id="task-1", candidate_id="candidate-1", candidate_hash="d" * 64),
        ),
        now=NOW,
    )
    assert plane.inspect_run("run-b")[0].cohort_id == f"cohort-a~{second_digest[:12]}"
    with pytest.raises(MixedCohortError):
        plane.assert_aggregateable(("run-a", "run-b"))

    artifact = ArtifactMetadataRecord(
        sha256="7" * 64,
        kind="stdout",
        size_bytes=4,
        media_type="text/plain",
        storage_uri=f"s3://verirun/sha256/77/{'7' * 64}",
        created_at=NOW,
    )
    assert plane.register_artifact(artifact) == artifact
    assert plane.get_artifact(artifact.sha256) == artifact


def test_postgres_concurrent_duplicate_commit_has_one_authoritative_result(
    plane: PostgresControlPlane,
) -> None:
    digest = freeze(plane, plan_id="plan-a")
    create(plane, digest)
    lease = plane.claim_task(
        run_id="run-a",
        worker_id="worker-a",
        attempt_id="attempt-a",
        lease_token="token-a",
        lease_seconds=30,
        now=NOW,
    )
    assert lease is not None

    def commit() -> bool:
        return plane.commit_result(
            "attempt-a",
            worker_id="worker-a",
            lease_token="token-a",
            result_digest="f" * 64,
            result_payload={"status": "passed"},
            now=NOW + timedelta(seconds=1),
        ).inserted

    with ThreadPoolExecutor(max_workers=2) as executor:
        inserted = sorted(executor.map(lambda _: commit(), range(2)))

    assert inserted == [False, True]
    assert len(plane.list_results("run-a")) == 1


def test_live_control_plane_smoke(tmp_path: Path) -> None:
    assert DSN is not None
    endpoint = os.environ.get("VERIRUN_TEST_S3_ENDPOINT")
    access_key = os.environ.get("VERIRUN_TEST_S3_ACCESS_KEY")
    secret_key = os.environ.get("VERIRUN_TEST_S3_SECRET_KEY")
    if not endpoint or not access_key or not secret_key:
        pytest.skip("live S3 test variables are unset")
    summary = run_control_plane_smoke(
        tmp_path,
        dsn=DSN,
        s3_endpoint=endpoint,
        s3_access_key=access_key,
        s3_secret_key=secret_key,
        s3_bucket="verirun-m3-pytest",
        s3_secure=False,
        s3_server_identity="minio-test",
    )
    assert summary["succeeded"] is True
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "REPORT.md").is_file()
