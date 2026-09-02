from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from verirun.control_plane import (
    AggregationPolicy,
    EvaluationIntent,
    FailureDomain,
    FinalResultConflictError,
    IdempotencyConflictError,
    InMemoryControlPlane,
    InvalidTransitionError,
    LateAttemptError,
    MixedCohortError,
    PlanCompilationError,
    PlanCompileRequest,
    PlanNotSchedulableError,
    PlanState,
    RunState,
    RunTaskInput,
    RunTaskState,
    TaskSpec,
    VerifierCatalogEntry,
    compile_verification_plan,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def compile_request(*, image_digest: str = "b" * 64) -> PlanCompileRequest:
    return PlanCompileRequest(
        task_spec=TaskSpec(
            task_id="HumanEval/0",
            task_family="python-function",
            verifier_tags=("tests",),
        ),
        evaluation_intent=EvaluationIntent(name="correctness"),
        verifier_catalog=(
            VerifierCatalogEntry(
                verifier_id="python-tests",
                task_families=("python-function",),
                intents=("correctness",),
                adapter="verirun.synthetic",
                version="v1",
                config_digest="a" * 64,
                image_digest=image_digest,
                required_evidence=("stderr", "stdout"),
                tags=("tests",),
                expected_sandbox_cpu_seconds=2.5,
                priority=10,
            ),
        ),
        aggregation_policy=AggregationPolicy(
            policy_id="pass-rate",
            version="v1",
            reducer="mean",
            config_digest="c" * 64,
        ),
        policy_revision="policy-2026-09-02",
        input_evidence_digests=("e" * 64,),
        comparison_cohort_id="cohort-a",
        candidate_count=3,
        max_concurrency=8,
    )


def frozen_plan(
    plane: InMemoryControlPlane,
    *,
    plan_id: str = "plan-a",
    image_digest: str = "b" * 64,
) -> tuple[str, int]:
    plan = compile_verification_plan(
        plan_id=plan_id,
        revision=1,
        request=compile_request(image_digest=image_digest),
        now=NOW,
    )
    plane.register_plan(plan)
    plane.transition_plan(plan_id, 1, PlanState.VALIDATED, reason="schema valid", now=NOW)
    frozen = plane.transition_plan(plan_id, 1, PlanState.FROZEN, reason="approved", now=NOW)
    return frozen.plan_digest, frozen.revision


def task(candidate_id: str = "candidate-a") -> RunTaskInput:
    return RunTaskInput(
        task_id="HumanEval/0",
        candidate_id=candidate_id,
        candidate_hash="d" * 64,
    )


def create_run(
    plane: InMemoryControlPlane,
    digest: str,
    *,
    run_id: str = "run-a",
    plan_id: str = "plan-a",
    command: str = "cmd-create-a",
) -> None:
    plane.create_run(
        idempotency_key=command,
        run_id=run_id,
        requested_cohort_id="cohort-a",
        plan_id=plan_id,
        plan_revision=1,
        plan_digest=digest,
        tasks=(task(),),
        now=NOW,
    )


def test_plan_compilation_is_deterministic_and_estimates_budget() -> None:
    request = compile_request()
    first = compile_verification_plan(plan_id="plan-a", revision=1, request=request, now=NOW)
    second = compile_verification_plan(
        plan_id="plan-b",
        revision=7,
        request=request.model_copy(
            update={"input_evidence_digests": tuple(reversed(request.input_evidence_digests))}
        ),
        now=NOW + timedelta(days=1),
    )

    assert first.plan_digest == second.plan_digest
    assert first.spec.required_evidence == ("stderr", "stdout")
    assert first.spec.budget_estimate.expected_sandbox_cpu_seconds == 7.5
    assert first.spec.budget_estimate.max_concurrency == 3


def test_plan_digest_changes_for_image_evidence_and_aggregation_policy() -> None:
    request = compile_request()
    baseline = compile_verification_plan(
        plan_id="baseline", revision=1, request=request, now=NOW
    ).plan_digest
    verifier = request.verifier_catalog[0]
    variants = (
        request.model_copy(
            update={"verifier_catalog": (verifier.model_copy(update={"image_digest": "9" * 64}),)}
        ),
        request.model_copy(
            update={
                "verifier_catalog": (verifier.model_copy(update={"required_evidence": ("junit",)}),)
            }
        ),
        request.model_copy(
            update={
                "aggregation_policy": request.aggregation_policy.model_copy(
                    update={"config_digest": "8" * 64}
                )
            }
        ),
    )
    variant_digests = {
        compile_verification_plan(
            plan_id=f"variant-{index}", revision=1, request=variant, now=NOW
        ).plan_digest
        for index, variant in enumerate(variants)
    }

    assert baseline not in variant_digests
    assert len(variant_digests) == len(variants)
    compiler_fields = PlanCompileRequest.model_fields
    assert "candidate" not in compiler_fields
    assert "model" not in compiler_fields
    assert "verification_result" not in compiler_fields


def test_plan_compiler_rejects_no_match_and_duplicate_verifier_ids() -> None:
    request = compile_request()
    with pytest.raises(PlanCompilationError, match="no verifier"):
        compile_verification_plan(
            plan_id="plan-none",
            revision=1,
            request=request.model_copy(
                update={
                    "task_spec": request.task_spec.model_copy(update={"task_family": "unsupported"})
                }
            ),
            now=NOW,
        )
    duplicate = request.verifier_catalog[0].model_copy(update={"priority": 20})
    with pytest.raises(PlanCompilationError, match="duplicate"):
        compile_verification_plan(
            plan_id="plan-duplicate",
            revision=1,
            request=request.model_copy(
                update={"verifier_catalog": (request.verifier_catalog[0], duplicate)}
            ),
            now=NOW,
        )


def test_only_validated_plan_can_freeze_and_only_frozen_plan_can_schedule() -> None:
    plane = InMemoryControlPlane()
    plan = compile_verification_plan(
        plan_id="plan-a", revision=1, request=compile_request(), now=NOW
    )
    plane.register_plan(plan)

    with pytest.raises(InvalidTransitionError, match="draft -> frozen"):
        plane.transition_plan("plan-a", 1, PlanState.FROZEN, reason="skip validation", now=NOW)
    with pytest.raises(PlanNotSchedulableError, match="only frozen"):
        create_run(plane, plan.plan_digest)

    plane.transition_plan("plan-a", 1, PlanState.VALIDATED, reason="valid", now=NOW)
    frozen = plane.transition_plan("plan-a", 1, PlanState.FROZEN, reason="approved", now=NOW)
    create_run(plane, frozen.plan_digest)
    run, tasks = plane.inspect_run("run-a")
    assert run.verification_plan_digest == frozen.plan_digest
    assert tasks[0].verification_plan_id == frozen.plan_id


def test_superseded_plan_stops_new_claims_for_existing_run() -> None:
    plane = InMemoryControlPlane()
    digest, _ = frozen_plan(plane)
    create_run(plane, digest)
    plane.transition_plan("plan-a", 1, PlanState.SUPERSEDED, reason="replacement frozen", now=NOW)

    with pytest.raises(PlanNotSchedulableError, match="no longer frozen"):
        plane.claim_task(
            run_id="run-a",
            worker_id="worker-a",
            attempt_id="attempt-a",
            lease_token="lease-a",
            lease_seconds=10,
            now=NOW,
        )


def test_invalid_plan_is_terminal_and_cannot_schedule() -> None:
    plane = InMemoryControlPlane()
    plan = compile_verification_plan(
        plan_id="plan-invalid", revision=1, request=compile_request(), now=NOW
    )
    plane.register_plan(plan)
    plane.transition_plan("plan-invalid", 1, PlanState.INVALID, reason="failed preflight", now=NOW)

    with pytest.raises(InvalidTransitionError, match="invalid -> validated"):
        plane.transition_plan(
            "plan-invalid", 1, PlanState.VALIDATED, reason="cannot recover", now=NOW
        )
    with pytest.raises(PlanNotSchedulableError, match="only frozen"):
        create_run(plane, plan.plan_digest, plan_id="plan-invalid")


def test_command_idempotency_replays_same_intent_and_rejects_different_intent() -> None:
    plane = InMemoryControlPlane()
    digest, _ = frozen_plan(plane)
    create_run(plane, digest)

    repeated = plane.create_run(
        idempotency_key="cmd-create-a",
        run_id="run-a",
        requested_cohort_id="cohort-a",
        plan_id="plan-a",
        plan_revision=1,
        plan_digest=digest,
        tasks=(task(),),
        now=NOW + timedelta(seconds=1),
    )
    assert repeated.run_id == "run-a"

    with pytest.raises(IdempotencyConflictError, match="different intent"):
        plane.create_run(
            idempotency_key="cmd-create-a",
            run_id="run-b",
            requested_cohort_id="cohort-a",
            plan_id="plan-a",
            plan_revision=1,
            plan_digest=digest,
            tasks=(task("candidate-b"),),
            now=NOW,
        )


def test_run_cohort_must_match_the_frozen_plan() -> None:
    plane = InMemoryControlPlane()
    digest, _ = frozen_plan(plane)

    with pytest.raises(PlanNotSchedulableError, match="cohort does not match"):
        plane.create_run(
            idempotency_key="wrong-cohort",
            run_id="run-wrong-cohort",
            requested_cohort_id="cohort-b",
            plan_id="plan-a",
            plan_revision=1,
            plan_digest=digest,
            tasks=(task(),),
            now=NOW,
        )


def test_changed_plan_splits_cohort_and_mixed_aggregation_is_rejected() -> None:
    plane = InMemoryControlPlane()
    first_digest, _ = frozen_plan(plane)
    second_digest, _ = frozen_plan(plane, plan_id="plan-b", image_digest="f" * 64)
    create_run(plane, first_digest)
    create_run(
        plane,
        second_digest,
        run_id="run-b",
        plan_id="plan-b",
        command="cmd-create-b",
    )

    first = plane.inspect_run("run-a")[0]
    second = plane.inspect_run("run-b")[0]
    assert first.cohort_id == "cohort-a"
    assert second.cohort_id == f"cohort-a~{second_digest[:12]}"
    with pytest.raises(MixedCohortError, match="one frozen plan"):
        plane.assert_aggregateable(("run-a", "run-b"))


def test_expired_attempt_is_reclaimed_and_late_result_cannot_commit() -> None:
    plane = InMemoryControlPlane()
    digest, _ = frozen_plan(plane)
    create_run(plane, digest)
    first = plane.claim_task(
        run_id="run-a",
        worker_id="worker-a",
        attempt_id="attempt-a",
        lease_token="lease-a",
        lease_seconds=5,
        now=NOW,
    )
    assert first is not None
    assert plane.reclaim_expired(now=NOW + timedelta(seconds=6)) == 1

    second = plane.claim_task(
        run_id="run-a",
        worker_id="worker-b",
        attempt_id="attempt-b",
        lease_token="lease-b",
        lease_seconds=30,
        now=NOW + timedelta(seconds=7),
    )
    assert second is not None
    assert second.verification_plan_id == "plan-a"
    with pytest.raises(LateAttemptError, match="no longer authoritative"):
        plane.commit_result(
            "attempt-a",
            worker_id="worker-a",
            lease_token="lease-a",
            result_digest="1" * 64,
            result_payload={"status": "passed"},
            now=NOW + timedelta(seconds=8),
        )


def test_heartbeat_extends_lease_and_duplicate_commit_is_effectively_once() -> None:
    plane = InMemoryControlPlane()
    digest, _ = frozen_plan(plane)
    create_run(plane, digest)
    lease = plane.claim_task(
        run_id="run-a",
        worker_id="worker-a",
        attempt_id="attempt-a",
        lease_token="lease-a",
        lease_seconds=5,
        now=NOW,
    )
    assert lease is not None
    heartbeat = plane.heartbeat(
        "attempt-a",
        worker_id="worker-a",
        lease_token="lease-a",
        lease_seconds=10,
        now=NOW + timedelta(seconds=4),
    )
    assert heartbeat.expires_at == NOW + timedelta(seconds=14)

    first = plane.commit_result(
        "attempt-a",
        worker_id="worker-a",
        lease_token="lease-a",
        result_digest="1" * 64,
        result_payload={"status": "passed"},
        now=NOW + timedelta(seconds=5),
    )
    repeated = plane.commit_result(
        "attempt-a",
        worker_id="worker-a",
        lease_token="lease-a",
        result_digest="1" * 64,
        result_payload={"status": "passed"},
        now=NOW + timedelta(seconds=6),
    )
    assert first.inserted is True
    assert first.result.verification_plan_id == "plan-a"
    assert repeated.inserted is False
    assert len(plane.list_results("run-a")) == 1
    assert plane.inspect_run("run-a")[0].state is RunState.COMPLETED

    with pytest.raises(FinalResultConflictError, match="different final result"):
        plane.commit_result(
            "attempt-a",
            worker_id="worker-a",
            lease_token="lease-a",
            result_digest="2" * 64,
            result_payload={"status": "failed"},
            failure_domain=FailureDomain.VERIFIER,
            now=NOW + timedelta(seconds=7),
        )
    with pytest.raises(FinalResultConflictError, match="different final result"):
        plane.commit_result(
            "attempt-a",
            worker_id="worker-a",
            lease_token="lease-a",
            result_digest="1" * 64,
            result_payload={"status": "same digest, changed payload"},
            now=NOW + timedelta(seconds=8),
        )


def test_cancel_resume_and_replay_keep_the_frozen_plan() -> None:
    plane = InMemoryControlPlane()
    digest, _ = frozen_plan(plane)
    create_run(plane, digest)
    plane.claim_task(
        run_id="run-a",
        worker_id="worker-a",
        attempt_id="attempt-a",
        lease_token="lease-a",
        lease_seconds=30,
        now=NOW,
    )

    cancelled = plane.cancel_run("run-a", idempotency_key="cmd-cancel", now=NOW)
    assert cancelled.state is RunState.CANCELLED
    assert plane.inspect_run("run-a")[1][0].state is RunTaskState.CANCELLED
    resumed = plane.resume_run("run-a", idempotency_key="cmd-resume", now=NOW)
    assert resumed.state is RunState.PENDING
    assert plane.inspect_run("run-a")[1][0].state is RunTaskState.QUEUED

    replay = plane.replay_run(
        "run-a", replay_run_id="run-replay", idempotency_key="cmd-replay", now=NOW
    )
    assert replay.source_run_id == "run-a"
    assert replay.verification_plan_digest == digest
    assert replay.cohort_id == "cohort-a"
