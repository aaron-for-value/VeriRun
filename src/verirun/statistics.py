"""Small, dependency-free paired evaluation statistics for reliability evidence."""

from __future__ import annotations

from math import comb, sqrt
from random import Random
from typing import Annotated, Literal

from pydantic import Field, model_validator

from verirun.canonical import content_hash
from verirun.models import FrozenModel, NonEmpty, Sha256
from verirun.reliability import ReliabilityPolicy

_WILSON_95_Z = 1.959963984540054


class CandidateTaskSamples(FrozenModel):
    """Independent samples for one candidate on one frozen task."""

    task_id: NonEmpty
    samples: tuple[bool, ...]
    infrastructure_excluded: bool = False

    @model_validator(mode="after")
    def validate_samples(self) -> CandidateTaskSamples:
        if not self.samples:
            raise ValueError("at least one sample is required")
        return self


class PairedTaskSamples(FrozenModel):
    """Matched candidate and baseline samples for one task in one cohort."""

    task_id: NonEmpty
    baseline: CandidateTaskSamples
    candidate: CandidateTaskSamples

    @model_validator(mode="after")
    def validate_pair(self) -> PairedTaskSamples:
        if self.baseline.task_id != self.task_id or self.candidate.task_id != self.task_id:
            raise ValueError("paired samples must retain their enclosing task_id")
        if len(self.baseline.samples) != len(self.candidate.samples):
            raise ValueError("paired samples require equal per-task sample counts")
        return self


class ConfidenceInterval(FrozenModel):
    level: Annotated[float, Field(ge=0, le=1)] = 0.95
    lower: Annotated[float, Field(ge=0, le=1)]
    upper: Annotated[float, Field(ge=0, le=1)]


class CandidateScore(FrozenModel):
    pass_at_1: Annotated[float, Field(ge=0, le=1)]
    pass_at_1_ci: ConfidenceInterval
    pass_at_k: tuple[tuple[Annotated[int, Field(gt=0)], Annotated[float, Field(ge=0, le=1)]], ...]


class PairedComparisonReport(FrozenModel):
    schema_version: Literal["verirun.paired-comparison/v1"] = "verirun.paired-comparison/v1"
    policy: ReliabilityPolicy
    policy_digest: Sha256
    cohort_id: NonEmpty
    paired_tasks: Annotated[int, Field(gt=0)]
    excluded_tasks: Annotated[int, Field(ge=0)]
    baseline: CandidateScore
    candidate: CandidateScore
    pass_at_1_delta: Annotated[float, Field(ge=-1, le=1)]
    pass_at_1_delta_ci: tuple[
        Annotated[float, Field(ge=-1, le=1)], Annotated[float, Field(ge=-1, le=1)]
    ]
    conclusion: Literal["sufficient", "insufficient"]
    conclusion_reasons: tuple[NonEmpty, ...]


def _wilson_interval(successes: int, total: int) -> ConfidenceInterval:
    if total <= 0:
        raise ValueError("total must be positive")
    proportion = successes / total
    denominator = 1 + _WILSON_95_Z**2 / total
    center = (proportion + _WILSON_95_Z**2 / (2 * total)) / denominator
    margin = (
        _WILSON_95_Z
        * sqrt((proportion * (1 - proportion) + _WILSON_95_Z**2 / (4 * total)) / total)
        / denominator
    )
    return ConfidenceInterval(lower=max(0, center - margin), upper=min(1, center + margin))


def _pass_at_k(samples: tuple[bool, ...], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    if len(samples) < k:
        raise ValueError("pass@k requires at least k samples for every task")
    successes = sum(samples)
    if len(samples) - successes < k:
        return 1.0
    return 1 - comb(len(samples) - successes, k) / comb(len(samples), k)


def _candidate_score(
    tasks: tuple[CandidateTaskSamples, ...], pass_at_k: tuple[int, ...]
) -> CandidateScore:
    flattened = tuple(sample for task in tasks for sample in task.samples)
    pass1 = sum(flattened) / len(flattened)
    return CandidateScore(
        pass_at_1=pass1,
        pass_at_1_ci=_wilson_interval(sum(flattened), len(flattened)),
        pass_at_k=tuple(
            (k, sum(_pass_at_k(task.samples, k) for task in tasks) / len(tasks))
            for k in sorted(set(pass_at_k))
        ),
    )


def _bootstrap_delta_ci(
    pairs: tuple[PairedTaskSamples, ...], *, iterations: int = 2_000, seed: int = 0
) -> tuple[float, float]:
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    random = Random(seed)
    deltas: list[float] = []
    for _ in range(iterations):
        sample = tuple(random.choice(pairs) for _ in range(len(pairs)))
        baseline = _candidate_score(tuple(pair.baseline for pair in sample), (1,)).pass_at_1
        candidate = _candidate_score(tuple(pair.candidate for pair in sample), (1,)).pass_at_1
        deltas.append(candidate - baseline)
    deltas.sort()
    lower_index = int(0.025 * (iterations - 1))
    upper_index = int(0.975 * (iterations - 1))
    return deltas[lower_index], deltas[upper_index]


def build_paired_comparison_report(
    pairs: tuple[PairedTaskSamples, ...],
    *,
    cohort_id: str,
    policy: ReliabilityPolicy,
    pass_at_k: tuple[int, ...] = (1,),
) -> PairedComparisonReport:
    """Score a matched cohort without turning insufficient data into a model claim."""

    if not pairs:
        raise ValueError("at least one paired task is required")
    if len({pair.task_id for pair in pairs}) != len(pairs):
        raise ValueError("paired task IDs must be unique")
    included = tuple(
        pair
        for pair in pairs
        if not (pair.baseline.infrastructure_excluded or pair.candidate.infrastructure_excluded)
    )
    if not included:
        raise ValueError("all paired tasks were excluded as infrastructure failures")
    baseline = _candidate_score(tuple(pair.baseline for pair in included), pass_at_k)
    candidate = _candidate_score(tuple(pair.candidate for pair in included), pass_at_k)
    delta = candidate.pass_at_1 - baseline.pass_at_1
    reasons: list[str] = []
    if len(included) < policy.min_paired_samples:
        reasons.append("paired_sample_threshold_not_met")
    exclusion_rate = (len(pairs) - len(included)) / len(pairs)
    if exclusion_rate > policy.max_infrastructure_exclusion_rate:
        reasons.append("infrastructure_exclusion_threshold_exceeded")
    return PairedComparisonReport(
        policy=policy,
        policy_digest=content_hash(policy),
        cohort_id=cohort_id,
        paired_tasks=len(included),
        excluded_tasks=len(pairs) - len(included),
        baseline=baseline,
        candidate=candidate,
        pass_at_1_delta=delta,
        pass_at_1_delta_ci=_bootstrap_delta_ci(included),
        conclusion="sufficient" if not reasons else "insufficient",
        conclusion_reasons=tuple(reasons) or ("paired_evidence_policy_satisfied",),
    )
