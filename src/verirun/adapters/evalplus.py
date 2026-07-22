"""Thin compatibility adapter for the fixed EvalPlus v0.3.1 public API."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Final, Literal, cast

from verirun.canonical import content_hash, sha256_bytes
from verirun.models import DatasetDigest, FrozenModel, NonEmpty, Sha256, VerificationStatus

EvalPlusDataset = Literal["humaneval", "mbpp"]
RawStatus = Literal["pass", "fail", "timeout"]

EVALPLUS_PACKAGE_RELEASE: Final = "v0.3.1"
EVALPLUS_TAG_COMMIT: Final = "e5d0ed0bab96280b60b637ec7f15b5e4841b0cb2"
HUMANEVAL_PLUS_RELEASE: Final = "v0.1.10"
MBPP_PLUS_RELEASE: Final = "v0.2.0"
DEFAULT_DATASET_RELEASE = {
    "humaneval": HUMANEVAL_PLUS_RELEASE,
    "mbpp": MBPP_PLUS_RELEASE,
}

Problem = dict[str, Any]
Problems = dict[str, Problem]
ExpectedOutputs = dict[str, dict[str, Any]]
Loader = Callable[..., Problems]
Hasher = Callable[..., str]
GroundTruth = Callable[[Problems, str, Sequence[str]], ExpectedOutputs]
Checker = Callable[..., dict[str, Any]]


class EvalPlusUnavailable(RuntimeError):
    """Raised when the optional EvalPlus dependency is not installed."""


class EvalPlusPhaseResult(FrozenModel):
    """Raw EvalPlus phase result without invented error categories."""

    status: RawStatus
    details: tuple[bool, ...]


class EvalPlusTaskResult(FrozenModel):
    """Stable, source-free evidence record for one candidate and task."""

    schema_version: Literal["verirun.evalplus-task-result/v1"] = "verirun.evalplus-task-result/v1"
    package_release: Literal["v0.3.1"] = EVALPLUS_PACKAGE_RELEASE
    package_commit: Literal["e5d0ed0bab96280b60b637ec7f15b5e4841b0cb2"] = EVALPLUS_TAG_COMMIT
    dataset: EvalPlusDataset
    dataset_release: NonEmpty
    dataset_digest: DatasetDigest
    subset_label: NonEmpty
    subset_digest: DatasetDigest
    task_id: NonEmpty
    candidate_id: NonEmpty
    candidate_hash: Sha256
    base: EvalPlusPhaseResult
    plus: EvalPlusPhaseResult
    mapped_status: VerificationStatus


@dataclass(frozen=True)
class EvalPlusApi:
    load_humaneval: Loader
    hash_humaneval: Hasher
    load_mbpp: Loader
    hash_mbpp: Hasher
    get_groundtruth: GroundTruth
    check_correctness: Checker
    mbpp_output_not_none_tasks: Sequence[str]


@dataclass(frozen=True)
class EvalPlusSubset:
    dataset: EvalPlusDataset
    dataset_release: str
    dataset_digest: str
    subset_label: str
    subset_digest: str
    task_ids: tuple[str, ...]
    problems: Problems
    expected_outputs: ExpectedOutputs
    check_correctness: Checker


def _load_api() -> EvalPlusApi:
    try:
        data = import_module("evalplus.data")
        evaluate = import_module("evalplus.evaluate")
        special_oracle = import_module("evalplus.eval._special_oracle")
    except ImportError as exc:
        raise EvalPlusUnavailable(
            "EvalPlus support is optional; install it with `pip install -e '.[evalplus]'`"
        ) from exc

    return EvalPlusApi(
        load_humaneval=cast(Loader, data.get_human_eval_plus),
        hash_humaneval=cast(Hasher, data.get_human_eval_plus_hash),
        load_mbpp=cast(Loader, data.get_mbpp_plus),
        hash_mbpp=cast(Hasher, data.get_mbpp_plus_hash),
        get_groundtruth=cast(GroundTruth, evaluate.get_groundtruth),
        check_correctness=cast(Checker, evaluate.check_correctness),
        mbpp_output_not_none_tasks=cast(Sequence[str], special_oracle.MBPP_OUTPUT_NOT_NONE_TASKS),
    )


def _qualify_upstream_digest(value: str) -> str:
    lowered = value.lower()
    if len(lowered) == 32 and all(character in "0123456789abcdef" for character in lowered):
        return f"md5:{lowered}"
    if len(lowered) == 64 and all(character in "0123456789abcdef" for character in lowered):
        return f"sha256:{lowered}"
    raise ValueError("EvalPlus returned an unsupported dataset digest")


def load_subset(
    dataset: EvalPlusDataset,
    task_ids: Sequence[str],
    *,
    subset_label: str,
) -> EvalPlusSubset:
    """Load a labeled subset and compute ground truth under a subset-specific cache key."""

    ordered_task_ids = tuple(task_ids)
    if not ordered_task_ids:
        raise ValueError("EvalPlus subset requires at least one task")
    if len(set(ordered_task_ids)) != len(ordered_task_ids):
        raise ValueError("EvalPlus subset task IDs must be unique")

    api = _load_api()
    if dataset == "humaneval":
        all_problems = api.load_humaneval(version="default")
        upstream_digest = api.hash_humaneval(version="default")
        output_not_none_tasks: Sequence[str] = ()
    else:
        all_problems = api.load_mbpp(version="default")
        upstream_digest = api.hash_mbpp(version="default")
        output_not_none_tasks = api.mbpp_output_not_none_tasks

    missing = [task_id for task_id in ordered_task_ids if task_id not in all_problems]
    if missing:
        raise ValueError(f"unknown EvalPlus task IDs: {', '.join(missing)}")

    dataset_digest = _qualify_upstream_digest(upstream_digest)
    subset_digest_raw = content_hash(
        {
            "dataset": dataset,
            "dataset_release": DEFAULT_DATASET_RELEASE[dataset],
            "dataset_digest": dataset_digest,
            "task_ids": ordered_task_ids,
        }
    )
    selected = {task_id: all_problems[task_id] for task_id in ordered_task_ids}
    expected = api.get_groundtruth(selected, subset_digest_raw, output_not_none_tasks)
    return EvalPlusSubset(
        dataset=dataset,
        dataset_release=DEFAULT_DATASET_RELEASE[dataset],
        dataset_digest=dataset_digest,
        subset_label=subset_label,
        subset_digest=f"sha256:{subset_digest_raw}",
        task_ids=ordered_task_ids,
        problems=selected,
        expected_outputs=expected,
        check_correctness=api.check_correctness,
    )


def _phase(value: object) -> EvalPlusPhaseResult:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("EvalPlus returned an invalid phase result")
    status, details = value
    if status not in {"pass", "fail", "timeout"}:
        raise ValueError(f"EvalPlus returned an unknown status: {status!r}")
    if not isinstance(details, Sequence) or isinstance(details, (str, bytes, bytearray)):
        raise ValueError("EvalPlus returned invalid per-input details")
    return EvalPlusPhaseResult(
        status=cast(RawStatus, status),
        details=tuple(bool(item) for item in details),
    )


def _mapped_status(base: EvalPlusPhaseResult, plus: EvalPlusPhaseResult) -> VerificationStatus:
    if base.status == plus.status == "pass":
        return VerificationStatus.PASSED
    if "timeout" in {base.status, plus.status}:
        return VerificationStatus.TIMEOUT
    return VerificationStatus.TEST_FAILURE


def evaluate_candidate(
    subset: EvalPlusSubset,
    *,
    task_id: str,
    candidate_id: str,
    solution: str,
) -> EvalPlusTaskResult:
    """Evaluate complete candidate source through EvalPlus's public correctness path."""

    if task_id not in subset.problems:
        raise ValueError(f"task {task_id!r} is not part of subset {subset.subset_label!r}")
    raw = subset.check_correctness(
        subset.dataset,
        0,
        subset.problems[task_id],
        solution,
        subset.expected_outputs[task_id],
        base_only=False,
        fast_check=False,
        identifier=candidate_id,
    )
    base = _phase(raw.get("base"))
    plus = _phase(raw.get("plus"))
    return EvalPlusTaskResult(
        dataset=subset.dataset,
        dataset_release=subset.dataset_release,
        dataset_digest=subset.dataset_digest,
        subset_label=subset.subset_label,
        subset_digest=subset.subset_digest,
        task_id=task_id,
        candidate_id=candidate_id,
        candidate_hash=sha256_bytes(solution.encode("utf-8")),
        base=base,
        plus=plus,
        mapped_status=_mapped_status(base, plus),
    )


def oracle_solution(subset: EvalPlusSubset, task_id: str) -> str:
    problem = subset.problems[task_id]
    return str(problem["prompt"]) + str(problem["canonical_solution"])


def obvious_failure_solution(subset: EvalPlusSubset, task_id: str) -> str:
    problem = subset.problems[task_id]
    return str(problem["prompt"]) + "    pass\n"
