from __future__ import annotations

import pytest

from verirun.reliability import (
    ReliabilityPolicy,
    ReliabilityValidity,
    TaskReliabilityObservation,
    TelemetryRecorder,
    build_reliability_report,
)
from verirun.statistics import (
    CandidateTaskSamples,
    PairedTaskSamples,
    build_paired_comparison_report,
)


def observation(
    task_id: str,
    *,
    attempts: int = 1,
    first: bool = True,
    final: bool = True,
    excluded: bool = False,
    complete: bool = True,
    failure_domain: str | None = None,
) -> TaskReliabilityObservation:
    return TaskReliabilityObservation(
        run_id="run-reliability",
        task_id=task_id,
        attempt_count=attempts,
        first_attempt_succeeded=first,
        final_succeeded=final,
        infrastructure_excluded=excluded,
        evidence_complete=complete,
        failure_domain=failure_domain,
        latency_ms=10,
        cost_units=0.5,
    )


def test_recorder_correlates_nested_spans_with_durable_identifiers() -> None:
    recorder = TelemetryRecorder()
    digest = "a" * 64
    artifact = "b" * 64
    with (
        recorder.span(
            "run",
            component="control-plane",
            run_id="run-reliability",
            verification_plan_digest=digest,
        ) as outer,
        recorder.span(
            "attempt",
            component="sandbox",
            run_id="run-reliability",
            task_id="task-1",
            attempt_id="attempt-1",
            verification_plan_digest=digest,
            artifact_sha256=artifact,
        ) as inner,
    ):
        recorder.add_metric("verirun.attempt.count", 1)

    snapshot = recorder.snapshot()
    events = snapshot["events"]
    assert isinstance(events, list)
    assert outer.trace_id == inner.trace_id
    assert outer.span_id != inner.span_id
    assert events[1]["attempt_id"] == "attempt-1"
    assert events[1]["comparison_cohort_id"] is None
    assert events[1]["artifact_sha256"] == artifact
    assert snapshot["metrics"] == {"verirun.attempt.count": 1.0}
    assert snapshot["span_count"] == 2


def test_report_marks_missing_lineage_invalid_and_keeps_raw_rates() -> None:
    report = build_reliability_report(
        (observation("task-1"), observation("task-2", complete=False)),
        ReliabilityPolicy(policy_id="reference", version="v1"),
    )

    assert report.validity is ReliabilityValidity.INVALID
    assert report.final_success_rate == 1
    assert report.retry_amplification == 1
    assert report.total_cost_units == 1
    assert len(report.policy_digest) == 64


def test_report_marks_excess_infrastructure_exclusions_partial() -> None:
    report = build_reliability_report(
        (
            observation(
                "task-1", excluded=True, first=False, final=False, failure_domain="storage"
            ),
            observation("task-2"),
        ),
        ReliabilityPolicy(
            policy_id="reference", version="v1", max_infrastructure_exclusion_rate=0.05
        ),
    )

    assert report.validity is ReliabilityValidity.PARTIAL
    assert report.failure_domain_counts == (("storage", 1),)
    assert report.first_attempt_success_rate == 0.5


def test_report_rejects_mixed_runs() -> None:
    second = observation("task-2").model_copy(update={"run_id": "another-run"})
    with pytest.raises(ValueError, match="one run"):
        build_reliability_report(
            (observation("task-1"), second), ReliabilityPolicy(policy_id="reference", version="v1")
        )


def _pairs(count: int, *, excluded: bool = False) -> tuple[PairedTaskSamples, ...]:
    return tuple(
        PairedTaskSamples(
            task_id=f"task-{index}",
            baseline=CandidateTaskSamples(
                task_id=f"task-{index}", samples=(index % 2 == 0,), infrastructure_excluded=excluded
            ),
            candidate=CandidateTaskSamples(
                task_id=f"task-{index}", samples=(True,), infrastructure_excluded=excluded
            ),
        )
        for index in range(count)
    )


def test_paired_statistics_marks_small_fixture_insufficient() -> None:
    report = build_paired_comparison_report(
        _pairs(2),
        cohort_id="fixture",
        policy=ReliabilityPolicy(policy_id="reference", version="v1", min_paired_samples=3),
    )

    assert report.conclusion == "insufficient"
    assert report.conclusion_reasons == ("paired_sample_threshold_not_met",)
    assert report.candidate.pass_at_1 == 1
    assert report.baseline.pass_at_1_ci.lower < report.baseline.pass_at_1
    assert len(report.policy_digest) == 64


def test_paired_statistics_applies_infrastructure_exclusion_policy() -> None:
    pairs = tuple(
        pair.model_copy(
            update={
                "baseline": pair.baseline.model_copy(update={"infrastructure_excluded": index > 0})
            }
        )
        for index, pair in enumerate(_pairs(30))
    )
    report = build_paired_comparison_report(
        pairs,
        cohort_id="fixture",
        policy=ReliabilityPolicy(policy_id="reference", version="v1"),
    )

    with pytest.raises(ValueError, match="all paired tasks"):
        build_paired_comparison_report(
            _pairs(1, excluded=True),
            cohort_id="all-excluded",
            policy=ReliabilityPolicy(policy_id="reference", version="v1"),
        )
    assert report.excluded_tasks == 29
    assert report.conclusion == "insufficient"
    assert "infrastructure_exclusion_threshold_exceeded" in report.conclusion_reasons
