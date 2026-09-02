"""Live PostgreSQL + S3 recovery evidence for M3."""

from __future__ import annotations

import platform
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import psycopg

from verirun.canonical import content_hash, write_canonical_json
from verirun.control_plane import (
    AggregationPolicy,
    EvaluationIntent,
    FailureDomain,
    LateAttemptError,
    MixedCohortError,
    PlanCompileRequest,
    PlanState,
    RunTaskInput,
    TaskSpec,
    VerifierCatalogEntry,
    compile_verification_plan,
)
from verirun.postgres import MIGRATION_VERSION, PostgresControlPlane
from verirun.provenance import source_state
from verirun.s3_artifacts import S3ArtifactStore


def _request(*, cohort_id: str, image_digest: str) -> PlanCompileRequest:
    return PlanCompileRequest(
        task_spec=TaskSpec(task_id="m3-recovery", task_family="python-function"),
        evaluation_intent=EvaluationIntent(name="correctness", required_verifier_tags=("tests",)),
        verifier_catalog=(
            VerifierCatalogEntry(
                verifier_id="python-tests",
                task_families=("python-function",),
                intents=("correctness",),
                adapter="verirun.synthetic",
                version="v1",
                config_digest="a" * 64,
                image_digest=image_digest,
                required_evidence=("stdout", "stderr"),
                tags=("tests",),
                expected_model_tokens=0,
                expected_sandbox_cpu_seconds=2.5,
            ),
        ),
        aggregation_policy=AggregationPolicy(
            policy_id="pass-rate",
            version="v1",
            reducer="mean",
            config_digest="c" * 64,
        ),
        policy_revision="m3-smoke-v1",
        input_evidence_digests=("e" * 64,),
        comparison_cohort_id=cohort_id,
        candidate_count=2,
        max_concurrency=2,
    )


def _freeze(
    plane: PostgresControlPlane,
    *,
    plan_id: str,
    cohort_id: str,
    image_digest: str,
    now: datetime,
) -> str:
    plan = compile_verification_plan(
        plan_id=plan_id,
        revision=1,
        request=_request(cohort_id=cohort_id, image_digest=image_digest),
        now=now,
    )
    plane.register_plan(plan)
    plane.transition_plan(plan_id, 1, PlanState.VALIDATED, reason="smoke validation", now=now)
    return plane.transition_plan(
        plan_id, 1, PlanState.FROZEN, reason="smoke freeze", now=now
    ).plan_digest


def _postgres_identity(dsn: str) -> str:
    with psycopg.connect(dsn) as connection:
        row = connection.execute("SHOW server_version").fetchone()
    assert row is not None
    return str(row[0])


def run_control_plane_smoke(
    output: Path,
    *,
    dsn: str,
    s3_endpoint: str,
    s3_access_key: str,
    s3_secret_key: str,
    s3_bucket: str,
    s3_secure: bool,
    s3_server_identity: str,
) -> dict[str, object]:
    """Run the M3 live recovery contract and write canonical evidence."""

    session = uuid4().hex[:12]
    now = datetime.now(UTC)
    cohort_id = f"m3-smoke-cohort-{session}"
    plan_id = f"m3-smoke-plan-{session}"
    run_id = f"m3-smoke-run-{session}"
    plane = PostgresControlPlane(dsn)
    plane.migrate()
    plan_digest = _freeze(
        plane,
        plan_id=plan_id,
        cohort_id=cohort_id,
        image_digest="b" * 64,
        now=now,
    )
    plan = plane.get_plan(plan_id, 1)
    plane.create_run(
        idempotency_key=f"create-{run_id}",
        run_id=run_id,
        requested_cohort_id=cohort_id,
        plan_id=plan_id,
        plan_revision=1,
        plan_digest=plan_digest,
        tasks=(
            RunTaskInput(task_id="task-a", candidate_id="candidate-a", candidate_hash="1" * 64),
            RunTaskInput(task_id="task-b", candidate_id="candidate-b", candidate_hash="2" * 64),
        ),
        now=now,
    )
    first_attempt = plane.claim_task(
        run_id=run_id,
        worker_id="worker-before-restart",
        attempt_id=f"attempt-old-{session}",
        lease_token=f"lease-old-{session}",
        lease_seconds=1,
        now=now,
    )
    assert first_attempt is not None

    restarted = PostgresControlPlane(dsn)
    recovered_run, recovered_tasks = restarted.inspect_run(run_id)
    reclaimed = restarted.reclaim_expired(now=now + timedelta(seconds=2))
    takeover = restarted.claim_task(
        run_id=run_id,
        worker_id="worker-after-restart",
        attempt_id=f"attempt-takeover-{session}",
        lease_token=f"lease-takeover-{session}",
        lease_seconds=30,
        now=now + timedelta(seconds=3),
    )
    assert takeover is not None
    late_rejected = False
    try:
        restarted.commit_result(
            first_attempt.attempt_id,
            worker_id=first_attempt.worker_id,
            lease_token=first_attempt.lease_token,
            result_digest="3" * 64,
            result_payload={"status": "late"},
            now=now + timedelta(seconds=4),
        )
    except LateAttemptError:
        late_rejected = True

    store = S3ArtifactStore.connect(
        endpoint=s3_endpoint,
        access_key=s3_access_key,
        secret_key=s3_secret_key,
        bucket=s3_bucket,
        secure=s3_secure,
        create_bucket=True,
    )
    artifact_ref, artifact_metadata = store.put_json(
        kind="verification-result",
        value={"status": "passed", "attempt_id": takeover.attempt_id},
        now=now + timedelta(seconds=4),
    )
    restarted.register_artifact(artifact_metadata)
    first_commit = restarted.commit_result(
        takeover.attempt_id,
        worker_id=takeover.worker_id,
        lease_token=takeover.lease_token,
        result_digest=artifact_ref.sha256,
        result_payload={"status": "passed", "artifact_sha256": artifact_ref.sha256},
        now=now + timedelta(seconds=4),
    )
    repeated_commit = restarted.commit_result(
        takeover.attempt_id,
        worker_id=takeover.worker_id,
        lease_token=takeover.lease_token,
        result_digest=artifact_ref.sha256,
        result_payload={"status": "passed", "artifact_sha256": artifact_ref.sha256},
        now=now + timedelta(seconds=5),
    )
    second = restarted.claim_task(
        run_id=run_id,
        worker_id="worker-after-restart",
        attempt_id=f"attempt-second-{session}",
        lease_token=f"lease-second-{session}",
        lease_seconds=30,
        now=now + timedelta(seconds=6),
    )
    assert second is not None
    restarted.commit_result(
        second.attempt_id,
        worker_id=second.worker_id,
        lease_token=second.lease_token,
        result_digest="4" * 64,
        result_payload={"status": "sandbox-failure"},
        failure_domain=FailureDomain.SANDBOX,
        now=now + timedelta(seconds=7),
    )

    changed_plan_id = f"m3-smoke-plan-changed-{session}"
    changed_digest = _freeze(
        restarted,
        plan_id=changed_plan_id,
        cohort_id=cohort_id,
        image_digest="9" * 64,
        now=now,
    )
    changed_run_id = f"m3-smoke-run-changed-{session}"
    changed_run = restarted.create_run(
        idempotency_key=f"create-{changed_run_id}",
        run_id=changed_run_id,
        requested_cohort_id=cohort_id,
        plan_id=changed_plan_id,
        plan_revision=1,
        plan_digest=changed_digest,
        tasks=(
            RunTaskInput(task_id="task-a", candidate_id="candidate-a", candidate_hash="1" * 64),
        ),
        now=now,
    )
    mixed_rejected = False
    try:
        restarted.assert_aggregateable((run_id, changed_run_id))
    except MixedCohortError:
        mixed_rejected = True

    final_run, final_tasks = restarted.inspect_run(run_id)
    results = restarted.list_results(run_id)
    object_round_trip = store.read_bytes(artifact_ref) is not None
    checks = {
        "frozen_plan_only": plan.state is PlanState.FROZEN,
        "restart_recovered_state": (
            recovered_run.verification_plan_digest == plan_digest and len(recovered_tasks) == 2
        ),
        "expired_lease_reclaimed": reclaimed == 1,
        "takeover_kept_plan": takeover.verification_plan_digest == plan_digest,
        "late_result_rejected": late_rejected,
        "duplicate_commit_effectively_once": (
            first_commit.inserted and not repeated_commit.inserted and len(results) == 2
        ),
        "run_completed": final_run.state.value == "completed"
        and all(task.state.value == "completed" for task in final_tasks),
        "artifact_round_trip": object_round_trip
        and restarted.get_artifact(artifact_ref.sha256) == artifact_metadata,
        "changed_plan_split_cohort": changed_run.cohort_id != final_run.cohort_id,
        "mixed_aggregation_rejected": mixed_rejected,
        "failure_domains_complete": {item.value for item in FailureDomain}
        == {"plan", "model", "user_code", "verifier", "sandbox", "scheduler", "storage"},
    }
    summary: dict[str, object] = {
        "schema_version": "verirun.control-plane-smoke/v1",
        "generated_at": now,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "postgresql": _postgres_identity(dsn),
            "psycopg": version("psycopg"),
            "minio_python": version("minio"),
            "s3_server_identity": s3_server_identity,
            "s3_bucket": s3_bucket,
        },
        "source": source_state(),
        "session": session,
        "plan_id": plan_id,
        "verification_plan_digest": plan_digest,
        "changed_verification_plan_digest": changed_digest,
        "run_id": run_id,
        "changed_run_id": changed_run_id,
        "artifact_sha256": artifact_ref.sha256,
        "migration_version": MIGRATION_VERSION,
        "failure_domains": [item.value for item in FailureDomain],
        "checks": checks,
        "succeeded": all(checks.values()),
    }
    summary["summary_digest"] = content_hash(summary)
    output.mkdir(parents=True, exist_ok=True)
    write_canonical_json(output / "summary.json", summary)
    report = [
        "# VeriRun M3 Durable Control Plane Smoke",
        "",
        f"- Succeeded: `{str(summary['succeeded']).lower()}`",
        f"- PostgreSQL: `{summary['environment']['postgresql']}`",  # type: ignore[index]
        f"- S3 server: `{s3_server_identity}`",
        f"- Plan digest: `{plan_digest}`",
        f"- Changed plan digest: `{changed_digest}`",
        f"- Artifact digest: `{artifact_ref.sha256}`",
        f"- Source revision: `{summary['source']['revision']}`",  # type: ignore[index]
        f"- Source working tree clean: `{str(summary['source']['working_tree_clean']).lower()}`",  # type: ignore[index]
        "",
        "## Checks",
        "",
    ]
    report.extend(f"- {name}: `{str(value).lower()}`" for name, value in checks.items())
    report.extend(
        [
            "",
            "## Boundary",
            "",
            "This smoke proves PostgreSQL persistence across control-plane client reconstruction, "
            "lease takeover, authoritative-result uniqueness, cohort splitting, and a live "
            "S3-compatible artifact round trip. It does not claim exactly-once execution.",
            "",
        ]
    )
    (output / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    return summary
