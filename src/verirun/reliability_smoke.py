"""M5 reliability evidence over the existing CPU trusted-fixture reference."""

from __future__ import annotations

import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from verirun.canonical import write_canonical_json
from verirun.distributed_smoke import (
    _run_once,
    distributed_concurrency_succeeded,
    run_distributed_concurrency_matrix,
    run_distributed_fault_smoke,
)
from verirun.provenance import source_state
from verirun.reliability import (
    ReliabilityPolicy,
    TaskReliabilityObservation,
    TelemetryRecorder,
    build_reliability_report,
)
from verirun.statistics import (
    CandidateTaskSamples,
    PairedTaskSamples,
    build_paired_comparison_report,
)


def _fixture_pairs() -> tuple[PairedTaskSamples, ...]:
    """Return fixed paired outcomes that test report mechanics, not a model."""

    return tuple(
        PairedTaskSamples(
            task_id=f"trusted-pair-{index:02d}",
            baseline=CandidateTaskSamples(
                task_id=f"trusted-pair-{index:02d}", samples=(index % 5 != 0,)
            ),
            candidate=CandidateTaskSamples(
                task_id=f"trusted-pair-{index:02d}", samples=(index % 8 != 0,)
            ),
        )
        for index in range(40)
    )


def _observations_from_run(run: dict[str, object]) -> tuple[TaskReliabilityObservation, ...]:
    plan_digest = str(run["plan_digest"])
    statuses = cast(list[str], run["result_statuses"])
    telemetry = cast(dict[str, object], run["telemetry"])
    events = cast(list[dict[str, object]], telemetry["events"])
    complete = any(
        event["name"] == "verirun.run.execute" and event["run_id"] == "m4-trusted-fixtures"
        for event in events
    ) and all(
        any(
            event["name"] == name
            and event["verification_plan_digest"] == plan_digest
            and event["run_id"] == "m4-trusted-fixtures"
            for event in events
        )
        for name in ("verirun.attempt.submit", "verirun.attempt.commit")
    )
    return tuple(
        TaskReliabilityObservation(
            run_id="m4-trusted-fixtures",
            task_id=f"trusted/{index}",
            attempt_count=1,
            first_attempt_succeeded=status == "passed",
            final_succeeded=status == "passed",
            evidence_complete=complete,
            latency_component="end_to_end",
            latency_ms=0,
        )
        for index, status in enumerate(statuses)
    )


def reliability_smoke_markdown(summary: dict[str, object]) -> str:
    checks = cast(dict[str, bool], summary["checks"])
    report = cast(dict[str, object], summary["reliability_report"])
    comparison = cast(dict[str, object], summary["paired_comparison"])
    fault_reference = cast(dict[str, object], summary["fault_reference"])
    fault_checks = cast(dict[str, bool], fault_reference["checks"])
    environment = cast(dict[str, object], summary["environment"])
    source = cast(dict[str, object], summary["source"])
    lines = [
        "# VeriRun v0.6 Reliability Evidence Report",
        "",
        "> This report proves telemetry, policy invalidation, and report mechanics on CPU",
        "> trusted fixtures. It is not a model score, provider SLO, hardware-capacity, or",
        "> production-reliability claim.",
        "",
        f"- Python: `{environment['python']}`",
        f"- Platform: `{environment['platform']}`",
        f"- Source revision: `{source['revision']}`",
        f"- Working tree clean at start: `{source['working_tree_clean']}`",
        f"- Reliability validity: `{report['validity']}`",
        f"- Paired-fixture conclusion: `{comparison['conclusion']}`",
        "",
        "## Contract checks",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    lines.extend(f"| {name} | {'pass' if value else 'fail'} |" for name, value in checks.items())
    lines.extend(
        [
            "",
            "## Fault-reference checks",
            "",
            "| Check | Result |",
            "|---|---|",
        ]
    )
    lines.extend(
        f"| {name} | {'pass' if value else 'fail'} |" for name, value in fault_checks.items()
    )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- The paired outcomes are fixed fixtures that validate the statistical pipeline only.",
            "- The 1/2/4/8/16 matrix is a logical scheduler reference, not a CPU capacity result.",
            "- Fault recovery is restricted to the local single-node Ray reference; no provider,",
            "  multi-node, HA, GPU, or external-model behavior is inferred.",
            "- A missing lineage, excessive infrastructure exclusion, or insufficient paired",
            "  sample count is marked invalid, partial, or insufficient by the policy.",
            "",
        ]
    )
    return "\n".join(lines)


def run_reliability_smoke(output: Path) -> dict[str, object]:
    """Write one transparent M5 evidence bundle from bounded local references."""

    source = source_state()
    policy = ReliabilityPolicy(policy_id="m5-cpu-trusted-fixtures", version="v1")
    baseline_recorder = TelemetryRecorder()
    replay_recorder = TelemetryRecorder()
    baseline = _run_once(worker_id="m5-telemetry-baseline", telemetry=baseline_recorder)
    replay = _run_once(worker_id="m5-telemetry-replay", telemetry=replay_recorder)
    reliability_report = build_reliability_report(_observations_from_run(baseline), policy)
    paired_comparison = build_paired_comparison_report(
        _fixture_pairs(), cohort_id="m5-trusted-paired-statistics", policy=policy
    )
    fault_reference = run_distributed_fault_smoke(output / "fault-reference")
    capacity_reference = run_distributed_concurrency_matrix(output / "capacity-reference")
    baseline_events = cast(dict[str, object], baseline["telemetry"])["events"]
    replay_events = cast(dict[str, object], replay["telemetry"])["events"]
    fault_checks = cast(dict[str, bool], fault_reference["checks"])
    checks = {
        "baseline_completed": baseline["run_state"] == "completed",
        "replay_completed": replay["run_state"] == "completed",
        "telemetry_captures_driver_path": len(cast(list[object], baseline_events)) >= 10
        and len(cast(list[object], replay_events)) >= 10,
        "frozen_plan_replayed": baseline["plan_digest"] == replay["plan_digest"],
        "reliability_policy_valid": reliability_report.validity.value == "valid",
        "paired_fixture_policy_sufficient": paired_comparison.conclusion == "sufficient",
        "fault_recovery_contract": all(
            fault_checks[name]
            for name in (
                "task_crash_recovered",
                "actor_crash_recovered",
                "storage_transient_recovered",
                "straggler_completed",
                "large_object_payloads_returned",
            )
        ),
        "logical_capacity_reference_complete": distributed_concurrency_succeeded(
            capacity_reference
        ),
    }
    summary: dict[str, object] = {
        "schema_version": "verirun.reliability-smoke/v1",
        "generated_at": datetime.now(UTC),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "reference_workload": "cpu-trusted-fixtures",
            "claim_boundary": (
                "local single-node reference; no model/provider/hardware-capacity claim"
            ),
        },
        "source": source,
        "policy": policy.model_dump(mode="json"),
        "baseline": baseline,
        "replay": replay,
        "reliability_report": reliability_report.model_dump(mode="json"),
        "paired_comparison": paired_comparison.model_dump(mode="json"),
        "fault_reference": fault_reference,
        "capacity_reference": capacity_reference,
        "checks": checks,
    }
    output.mkdir(parents=True, exist_ok=True)
    write_canonical_json(output / "summary.json", summary)
    (output / "REPORT.md").write_text(reliability_smoke_markdown(summary), encoding="utf-8")
    return summary


def reliability_smoke_succeeded(summary: dict[str, object]) -> bool:
    checks = summary["checks"]
    return isinstance(checks, dict) and all(value is True for value in checks.values())
