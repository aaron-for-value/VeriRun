"""Correlated telemetry and policy-bound reliability evidence for v0.6."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import Annotated, Literal

from opentelemetry.metrics import Counter as OtelCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import Field, model_validator

from verirun.canonical import content_hash
from verirun.models import FrozenModel, NonEmpty, Sha256


class ReliabilityValidity(StrEnum):
    VALID = "valid"
    PARTIAL = "partial"
    INVALID = "invalid"


class TraceContext(FrozenModel):
    trace_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    span_id: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


class TelemetryEvent(FrozenModel):
    schema_version: Literal["verirun.telemetry-event/v1"] = "verirun.telemetry-event/v1"
    name: NonEmpty
    component: NonEmpty
    trace: TraceContext
    run_id: NonEmpty
    task_id: NonEmpty | None = None
    attempt_id: NonEmpty | None = None
    verification_plan_digest: Sha256 | None = None
    comparison_cohort_id: NonEmpty | None = None
    artifact_sha256: Sha256 | None = None
    attributes: tuple[tuple[NonEmpty, str], ...] = ()


class ReliabilityPolicy(FrozenModel):
    schema_version: Literal["verirun.reliability-policy/v1"] = "verirun.reliability-policy/v1"
    policy_id: NonEmpty
    version: NonEmpty
    max_infrastructure_exclusion_rate: Annotated[float, Field(ge=0, le=1)] = 0.05
    min_paired_samples: Annotated[int, Field(gt=0)] = 30
    require_complete_lineage: bool = True


class TaskReliabilityObservation(FrozenModel):
    run_id: NonEmpty
    task_id: NonEmpty
    attempt_count: Annotated[int, Field(gt=0)]
    first_attempt_succeeded: bool
    final_succeeded: bool
    infrastructure_excluded: bool = False
    evidence_complete: bool = True
    failure_domain: NonEmpty | None = None
    latency_component: NonEmpty = "end_to_end"
    latency_ms: Annotated[int, Field(ge=0)]
    cost_units: Annotated[float, Field(ge=0)] = 0


class ReliabilityReport(FrozenModel):
    schema_version: Literal["verirun.reliability-report/v1"] = "verirun.reliability-report/v1"
    run_id: NonEmpty
    policy: ReliabilityPolicy
    policy_digest: Sha256
    validity: ReliabilityValidity
    validity_reasons: tuple[NonEmpty, ...]
    total_tasks: Annotated[int, Field(gt=0)]
    excluded_tasks: Annotated[int, Field(ge=0)]
    first_attempt_success_rate: Annotated[float, Field(ge=0, le=1)]
    final_success_rate: Annotated[float, Field(ge=0, le=1)]
    retry_amplification: Annotated[float, Field(ge=1)]
    latency_by_component_ms: tuple[tuple[NonEmpty, int], ...]
    failure_domain_counts: tuple[tuple[NonEmpty, int], ...]
    total_cost_units: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def validate_exclusions(self) -> ReliabilityReport:
        if self.excluded_tasks > self.total_tasks:
            raise ValueError("excluded_tasks cannot exceed total_tasks")
        return self


class TelemetryRecorder:
    """In-process OTel recorder with deterministic, serializable correlation events."""

    def __init__(self) -> None:
        self._exporter = InMemorySpanExporter()
        self._provider = TracerProvider(resource=Resource.create({"service.name": "verirun"}))
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        self._tracer = self._provider.get_tracer("verirun.reliability")
        self._metric_reader = InMemoryMetricReader()
        self._meter_provider = MeterProvider(
            metric_readers=[self._metric_reader],
            resource=Resource.create({"service.name": "verirun"}),
        )
        self._meter = self._meter_provider.get_meter("verirun.reliability")
        self._counters: dict[str, OtelCounter] = {}
        self._events: list[TelemetryEvent] = []
        self._metrics: defaultdict[str, float] = defaultdict(float)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        component: str,
        run_id: str,
        task_id: str | None = None,
        attempt_id: str | None = None,
        verification_plan_digest: str | None = None,
        comparison_cohort_id: str | None = None,
        artifact_sha256: str | None = None,
    ) -> Iterator[TraceContext]:
        attributes: dict[str, str] = {"verirun.component": component, "verirun.run_id": run_id}
        for key, value in {
            "verirun.task_id": task_id,
            "verirun.attempt_id": attempt_id,
            "verirun.verification_plan_digest": verification_plan_digest,
            "verirun.comparison_cohort_id": comparison_cohort_id,
            "verirun.artifact_sha256": artifact_sha256,
        }.items():
            if value is not None:
                attributes[key] = value
        with self._tracer.start_as_current_span(name, attributes=attributes) as span:
            context = span.get_span_context()
            link = TraceContext(
                trace_id=f"{context.trace_id:032x}", span_id=f"{context.span_id:016x}"
            )
            self._events.append(
                TelemetryEvent(
                    name=name,
                    component=component,
                    trace=link,
                    run_id=run_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    verification_plan_digest=verification_plan_digest,
                    comparison_cohort_id=comparison_cohort_id,
                    artifact_sha256=artifact_sha256,
                )
            )
            try:
                yield link
            except BaseException:
                span.set_attribute("verirun.outcome", "error")
                raise
            else:
                span.set_attribute("verirun.outcome", "ok")

    def add_metric(self, name: str, value: float) -> None:
        counter = self._counters.get(name)
        if counter is None:
            counter = self._meter.create_counter(name)
            self._counters[name] = counter
        # Counters deliberately only model monotonic event quantities.  The local
        # aggregate below remains the evidence representation used by reports.
        if value >= 0:
            counter.add(value)
        self._metrics[name] += value

    def record_latency(self, component: str, latency_ms: int) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        self.add_metric(f"verirun.{component}.latency_ms", latency_ms)

    def snapshot(self) -> dict[str, object]:
        return {
            "schema_version": "verirun.telemetry-snapshot/v1",
            "events": [event.model_dump(mode="json") for event in self._events],
            "metrics": dict(sorted(self._metrics.items())),
            "span_count": len(self._exporter.get_finished_spans()),
        }


def build_reliability_report(
    observations: tuple[TaskReliabilityObservation, ...], policy: ReliabilityPolicy
) -> ReliabilityReport:
    """Produce an auditable run-validity decision from task-level evidence."""

    if not observations:
        raise ValueError("at least one task observation is required")
    run_ids = {item.run_id for item in observations}
    if len(run_ids) != 1:
        raise ValueError("reliability observations must belong to one run")
    total = len(observations)
    excluded = sum(item.infrastructure_excluded for item in observations)
    incomplete = [item.task_id for item in observations if not item.evidence_complete]
    reasons: list[str] = []
    if policy.require_complete_lineage and incomplete:
        validity = ReliabilityValidity.INVALID
        reasons.append(f"missing_evidence:{','.join(sorted(incomplete))}")
    elif excluded / total > policy.max_infrastructure_exclusion_rate:
        validity = ReliabilityValidity.PARTIAL
        reasons.append("infrastructure_exclusion_threshold_exceeded")
    else:
        validity = ReliabilityValidity.VALID
        reasons.append("evidence_complete_within_exclusion_policy")
    latency_by_component: Counter[str] = Counter()
    for item in observations:
        latency_by_component[item.latency_component] += item.latency_ms
    failure_domains = Counter(
        item.failure_domain for item in observations if item.failure_domain is not None
    )
    return ReliabilityReport(
        run_id=observations[0].run_id,
        policy=policy,
        policy_digest=content_hash(policy),
        validity=validity,
        validity_reasons=tuple(reasons),
        total_tasks=total,
        excluded_tasks=excluded,
        first_attempt_success_rate=(
            sum(item.first_attempt_succeeded for item in observations) / total
        ),
        final_success_rate=sum(item.final_succeeded for item in observations) / total,
        retry_amplification=sum(item.attempt_count for item in observations) / total,
        latency_by_component_ms=tuple(sorted(latency_by_component.items())),
        failure_domain_counts=tuple(sorted((key, value) for key, value in failure_domains.items())),
        total_cost_units=sum(item.cost_units for item in observations),
    )
