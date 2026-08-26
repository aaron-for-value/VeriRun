"""Full-workload EvalPlus evidence for the M0 protocol gate.

The workflow deliberately evaluates deterministic fixtures, not model outputs.  It
therefore demonstrates adapter lineage, Base/Plus semantics, and replay behavior
without making a capability or leaderboard claim.
"""

from __future__ import annotations

import os
import platform
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from verirun.adapters.evalplus import (
    EvalPlusDataset,
    EvalPlusSubset,
    EvalPlusTaskResult,
    evaluate_candidate,
    load_subset,
    obvious_failure_solution,
    oracle_solution,
    standard_task_ids,
)
from verirun.canonical import content_hash, sha256_bytes, write_canonical_json
from verirun.provenance import source_state

CandidateRecipe = Literal["oracle", "obvious-failure", "plus-boundary"]
StandardCandidateRecipe = Literal["oracle", "obvious-failure"]
STANDARD_DATASETS: tuple[EvalPlusDataset, ...] = ("humaneval", "mbpp")
STANDARD_RECIPES: tuple[StandardCandidateRecipe, ...] = ("oracle", "obvious-failure")

# EvalPlus validates `find_zero` by residual rather than by exact canonical output.
# The frozen canonical source has a reproducible failure under that verifier, so the
# standard workload retains it and records the expected exception transparently.
CANONICAL_REFERENCE_EXCEPTIONS: dict[tuple[EvalPlusDataset, str], str] = {
    ("humaneval", "HumanEval/32"): "The pinned canonical source fails the EvalPlus "
    "find_zero residual verifier; retain the task and report the reproducible failure.",
}


@dataclass(frozen=True)
class BoundaryFixture:
    """A deterministic fixture which fails one Plus-only input by construction."""

    dataset: EvalPlusDataset
    task_id: str


# The tasks use ordinary literal argument values and are intentionally split across
# HumanEval+ and MBPP+.  The selected Plus-only argument is derived deterministically
# from the pinned upstream task record and represented by a hash in public evidence.
BOUNDARY_FIXTURES: tuple[BoundaryFixture, ...] = (
    BoundaryFixture("humaneval", "HumanEval/0"),
    BoundaryFixture("humaneval", "HumanEval/1"),
    BoundaryFixture("humaneval", "HumanEval/2"),
    BoundaryFixture("mbpp", "Mbpp/2"),
    BoundaryFixture("mbpp", "Mbpp/3"),
)


def _task_ids(dataset: EvalPlusDataset) -> tuple[str, ...]:
    """Resolve the ordered standard workload from EvalPlus's pinned public API."""

    # `load_subset` validates all IDs against the same upstream loader. Keeping the
    # versioned task list in the generated manifest catches unexpected membership
    # changes before readers compare aggregate scores.
    return standard_task_ids(dataset)


def _plus_only_input(problem: dict[str, Any]) -> list[Any]:
    base_inputs = problem["base_input"]
    for candidate in problem["plus_input"]:
        if candidate not in base_inputs:
            if not isinstance(candidate, list):
                raise ValueError("EvalPlus input must be a positional-argument list")
            return candidate
    raise ValueError("boundary fixture requires an input present only in Plus")


def plus_boundary_solution(subset: EvalPlusSubset, task_id: str) -> tuple[str, str]:
    """Return source and a SHA-256 identity for a Base-pass/Plus-fail fixture."""

    problem = subset.problems[task_id]
    entry_point = str(problem["entry_point"])
    plus_only_input = _plus_only_input(problem)
    original_name = f"_verirun_original_{entry_point}"
    source = oracle_solution(subset, task_id)
    source += (
        f"\n{original_name} = {entry_point}\n"
        f"def {entry_point}(*args, **kwargs):\n"
        f"    if not kwargs and list(args) == {plus_only_input!r}:\n"
        "        raise AssertionError('verirun m0 plus-boundary fixture')\n"
        f"    return {original_name}(*args, **kwargs)\n"
    )
    return source, f"sha256:{sha256_bytes(repr(plus_only_input).encode('utf-8'))}"


def _case_directory(output: Path, dataset: str, task_id: str) -> Path:
    return output / "cases" / dataset / task_id.replace("/", "_")


def _case_record(
    result: EvalPlusTaskResult,
    replay: EvalPlusTaskResult,
    *,
    recipe: CandidateRecipe,
    expected_base: str,
    expected_plus: str,
    plus_only_input_digest: str | None = None,
) -> dict[str, Any]:
    replay_matched = content_hash(result) == content_hash(replay)
    return {
        "dataset": result.dataset,
        "task_id": result.task_id,
        "candidate_id": result.candidate_id,
        "candidate_hash": result.candidate_hash,
        "recipe": recipe,
        "base_status": result.base.status,
        "plus_status": result.plus.status,
        "base_passed_inputs": sum(result.base.details),
        "base_total_inputs": len(result.base.details),
        "plus_passed_inputs": sum(result.plus.details),
        "plus_total_inputs": len(result.plus.details),
        "expected_base": expected_base,
        "expected_plus": expected_plus,
        "expected_matched": (
            result.base.status == expected_base and result.plus.status == expected_plus
        ),
        "replay_matched": replay_matched,
        "result_hash": content_hash(result),
        "replay_hash": content_hash(replay),
        "plus_only_input_digest": plus_only_input_digest,
    }


def _write_case(
    output: Path,
    result: EvalPlusTaskResult,
    replay: EvalPlusTaskResult,
    *,
    recipe: CandidateRecipe,
) -> None:
    directory = _case_directory(output, result.dataset, result.task_id)
    write_canonical_json(directory / f"{recipe}-baseline.json", result)
    write_canonical_json(directory / f"{recipe}-replay.json", replay)


def _existing_pair(
    output: Path,
    *,
    dataset: str,
    task_id: str,
    recipe: CandidateRecipe,
    candidate_id: str,
    candidate_hash: str,
) -> tuple[EvalPlusTaskResult, EvalPlusTaskResult] | None:
    """Load a complete matching pair, or make an interrupted output explicit."""

    directory = _case_directory(output, dataset, task_id)
    baseline_path = directory / f"{recipe}-baseline.json"
    replay_path = directory / f"{recipe}-replay.json"
    paths = (baseline_path, replay_path)
    present = tuple(path.is_file() for path in paths)
    if not any(present):
        return None
    if not all(present):
        raise ValueError(
            f"incomplete prior evidence for {dataset}/{task_id}/{recipe}; "
            "remove the incomplete pair before resuming"
        )
    baseline = EvalPlusTaskResult.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    replay = EvalPlusTaskResult.model_validate_json(replay_path.read_text(encoding="utf-8"))
    for result in (baseline, replay):
        if result.candidate_id != candidate_id or result.candidate_hash != candidate_hash:
            raise ValueError(
                f"prior evidence for {dataset}/{task_id}/{recipe} belongs to a different candidate"
            )
    return baseline, replay


def _run_candidate(
    output: Path,
    subset: EvalPlusSubset,
    *,
    recipe: CandidateRecipe,
    factory: Callable[[EvalPlusSubset, str], tuple[str, str | None]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for task_id in subset.task_ids:
        source, input_digest = factory(subset, task_id)
        candidate_id = f"m0:{subset.dataset}:{task_id}:{recipe}"
        candidate_hash = sha256_bytes(source.encode("utf-8"))
        prior = _existing_pair(
            output,
            dataset=subset.dataset,
            task_id=task_id,
            recipe=recipe,
            candidate_id=candidate_id,
            candidate_hash=candidate_hash,
        )
        if prior is None:
            result = evaluate_candidate(
                subset,
                task_id=task_id,
                candidate_id=candidate_id,
                solution=source,
            )
            replay = evaluate_candidate(
                subset,
                task_id=task_id,
                candidate_id=candidate_id,
                solution=source,
            )
            _write_case(output, result, replay, recipe=recipe)
        else:
            result, replay = prior
        expected_base, expected_plus = _expected_outcomes(subset.dataset, task_id, recipe)
        records.append(
            _case_record(
                result,
                replay,
                recipe=recipe,
                expected_base=expected_base,
                expected_plus=expected_plus,
                plus_only_input_digest=input_digest,
            )
        )
    return records


def _oracle_factory(subset: EvalPlusSubset, task_id: str) -> tuple[str, None]:
    return oracle_solution(subset, task_id), None


def _obvious_failure_factory(subset: EvalPlusSubset, task_id: str) -> tuple[str, None]:
    return obvious_failure_solution(subset, task_id), None


def _expected_outcomes(
    dataset: EvalPlusDataset,
    task_id: str,
    recipe: CandidateRecipe,
) -> tuple[str, str]:
    if recipe == "plus-boundary":
        return "pass", "fail"
    if recipe == "obvious-failure":
        return "fail", "fail"
    if (dataset, task_id) in CANONICAL_REFERENCE_EXCEPTIONS:
        return "fail", "fail"
    return "pass", "pass"


def _boundary_factory(subset: EvalPlusSubset, task_id: str) -> tuple[str, str]:
    return plus_boundary_solution(subset, task_id)


def _cohort_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    task_count = len(records)
    base_passes = sum(record["base_status"] == "pass" for record in records)
    plus_passes = sum(record["plus_status"] == "pass" for record in records)
    return {
        "dataset": records[0]["dataset"],
        "recipe": records[0]["recipe"],
        "task_count": task_count,
        "base_passes": base_passes,
        "plus_passes": plus_passes,
        "base_pass_rate": base_passes / task_count,
        "plus_pass_rate": plus_passes / task_count,
        "base_minus_plus_pass_rate": (base_passes - plus_passes) / task_count,
        "expected_outcomes_matched": all(record["expected_matched"] for record in records),
        "semantic_replays_matched": all(record["replay_matched"] for record in records),
    }


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# VeriRun M0 EvalPlus full-workload evidence",
        "",
        "> This is reproducibility evidence for deterministic fixtures, not a model score,",
        "> leaderboard result, or hostile-code sandbox claim.",
        "",
        "## Reproduction contract",
        "",
        f"- EvalPlus package: `{summary['package_release']}` at `{summary['package_commit']}`",
        f"- Source revision: `{summary['source']['revision']}`",
        f"- Working tree clean at start: `{summary['source']['working_tree_clean']}`",
        f"- Python: `{summary['environment']['python']}` on `{summary['environment']['platform']}`",
        f"- EvalPlus max memory bytes: `{summary['environment']['evalplus_max_memory_bytes']}`",
        "- Protocol: standard ordered workloads for oracle and obvious-failure fixtures;",
        "  a separate five-task boundary-fixture cohort demonstrates Plus-only catches.",
        "",
        "## Base / Plus aggregate results",
        "",
        "| Dataset | Fixture | Tasks | Base pass | Plus pass | Base - Plus | Expected | Replay |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for cohort in summary["standard_cohorts"]:
        lines.append(
            "| `{dataset}` | `{recipe}` | {task_count} | {base_passes}/{task_count} "
            "({base_pass_rate:.1%}) | {plus_passes}/{task_count} ({plus_pass_rate:.1%}) "
            "| {base_minus_plus_pass_rate:.1%} | `{expected_outcomes_matched}` | "
            "`{semantic_replays_matched}` |".format(**cohort)
        )
    lines.extend(
        [
            "",
            "## Plus-only boundary fixtures",
            "",
            "Each fixture starts from the official canonical solution, then raises only for one",
            "input in the pinned Plus input set and absent from its Base input set.",
            "The input itself is represented by a SHA-256 digest so the report does not duplicate",
            "upstream test data.",
            "",
            "| Task | Base | Plus | Plus-only input digest | Replay |",
            "|---|---|---|---|---|",
        ]
    )
    for record in summary["boundary_fixtures"]:
        lines.append(
            "| `{task_id}` | `{base_status}` | `{plus_status}` | "
            "`{plus_only_input_digest}` | `{replay_matched}` |".format(**record)
        )
    lines.extend(
        [
            "",
            "## Audit layout",
            "",
            "- `manifest.json` freezes the ordered task sets, dataset digests, fixture contract,",
            "  environment, and source identity.",
            "- `summary.json` contains aggregate rates and hashes for every baseline/replay pair.",
            "- `cases/<dataset>/<task>/` contains source-free structured EvalPlus records for",
            "  each baseline and replay. Candidate source is deterministically reconstructed",
            "  from the pinned dataset and its SHA-256 is recorded in each record.",
            "",
            "## Limitations",
            "",
            "- These fixtures validate adapter behavior and replay provenance, not capability.",
            "- The local EvalPlus path runs only trusted, deterministic fixtures and is not a",
            "  hostile-code isolation or resource-enforcement claim.",
            "- On Darwin, this evidence uses `EVALPLUS_MAX_MEMORY_BYTES=-1` because the pinned",
            "  EvalPlus release cannot apply its default memory rlimit there; this exception is",
            "  recorded above and does not transfer to later Linux sandbox evidence.",
            "",
        ]
    )
    exceptions = summary["canonical_reference_exceptions"]
    if exceptions:
        lines.extend(
            [
                "## Canonical-reference exceptions",
                "",
                "The task remains in the standard workload. The exception is versioned in the",
                "manifest and source-free result records carry the resulting candidate hash.",
                "",
            ]
        )
        for exception in exceptions:
            lines.append(
                f"- `{exception['dataset']}/{exception['task_id']}`: {exception['reason']}"
            )
        lines.append("")
    return "\n".join(lines)


def run_m0_evalplus(
    output: Path,
    *,
    datasets: tuple[EvalPlusDataset, ...] = STANDARD_DATASETS,
    recipes: tuple[StandardCandidateRecipe, ...] = STANDARD_RECIPES,
    include_boundary: bool = True,
) -> dict[str, Any]:
    """Run full or resumable partial M0 evidence.

    A partial selection writes only complete task pairs.  Only the complete default
    selection writes the public manifest, aggregate summary, and Markdown report.
    """

    output.mkdir(parents=True, exist_ok=True)
    source = source_state()
    standard_cohorts: list[dict[str, Any]] = []
    manifest_datasets: list[dict[str, Any]] = []

    for dataset in datasets:
        task_ids = _task_ids(dataset)
        subset = load_subset(
            dataset,
            task_ids,
            subset_label=f"verirun-m0-{dataset}-standard-workload",
        )
        manifest_datasets.append(
            {
                "dataset": dataset,
                "dataset_release": subset.dataset_release,
                "dataset_digest": subset.dataset_digest,
                "task_count": len(subset.task_ids),
                "task_ids": subset.task_ids,
                "workload_digest": subset.subset_digest,
            }
        )
        if "oracle" in recipes:
            standard_cohorts.append(
                _cohort_summary(
                    _run_candidate(
                        output,
                        subset,
                        recipe="oracle",
                        factory=_oracle_factory,
                    )
                )
            )
        if "obvious-failure" in recipes:
            standard_cohorts.append(
                _cohort_summary(
                    _run_candidate(
                        output,
                        subset,
                        recipe="obvious-failure",
                        factory=_obvious_failure_factory,
                    )
                )
            )

    boundary_records: list[dict[str, Any]] = []
    if include_boundary:
        for fixture in BOUNDARY_FIXTURES:
            subset = load_subset(
                fixture.dataset,
                (fixture.task_id,),
                subset_label="verirun-m0-plus-boundary-fixtures",
            )
            boundary_records.extend(
                _run_candidate(
                    output,
                    subset,
                    recipe="plus-boundary",
                    factory=_boundary_factory,
                )
            )

    manifest: dict[str, Any] = {
        "schema_version": "verirun.m0-evalplus-manifest/v1",
        "generated_at": datetime.now(UTC),
        "source": source,
        "package_release": "v0.3.1",
        "package_commit": "e5d0ed0bab96280b60b637ec7f15b5e4841b0cb2",
        "datasets": manifest_datasets,
        "standard_candidate_recipes": STANDARD_RECIPES,
        "canonical_reference_exceptions": [
            {
                "dataset": dataset,
                "task_id": task_id,
                "reason": reason,
            }
            for (dataset, task_id), reason in CANONICAL_REFERENCE_EXCEPTIONS.items()
            if dataset in datasets
        ],
        "boundary_fixtures": [
            {"dataset": fixture.dataset, "task_id": fixture.task_id}
            for fixture in BOUNDARY_FIXTURES
        ],
        "replay_count": 2,
    }
    complete_selection = (
        datasets == STANDARD_DATASETS and recipes == STANDARD_RECIPES and include_boundary
    )
    summary: dict[str, Any] = {
        "schema_version": "verirun.m0-evalplus-summary/v1",
        "generated_at": datetime.now(UTC),
        "source": source,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "implementation": platform.python_implementation(),
            "evalplus_max_memory_bytes": os.environ.get(
                "EVALPLUS_MAX_MEMORY_BYTES", "upstream-default"
            ),
        },
        "package_release": "v0.3.1",
        "package_commit": "e5d0ed0bab96280b60b637ec7f15b5e4841b0cb2",
        "complete_selection": complete_selection,
        "manifest_hash": f"sha256:{content_hash(manifest)}" if complete_selection else None,
        "standard_cohorts": standard_cohorts,
        "canonical_reference_exceptions": manifest["canonical_reference_exceptions"],
        "boundary_fixtures": boundary_records,
        "expected_outcomes_matched": all(
            cohort["expected_outcomes_matched"] for cohort in standard_cohorts
        )
        and all(record["expected_matched"] for record in boundary_records),
        "semantic_replays_matched": all(
            cohort["semantic_replays_matched"] for cohort in standard_cohorts
        )
        and all(record["replay_matched"] for record in boundary_records),
        "plus_boundary_catches": sum(
            record["base_status"] == "pass" and record["plus_status"] == "fail"
            for record in boundary_records
        ),
    }
    if complete_selection:
        write_canonical_json(output / "manifest.json", manifest)
        write_canonical_json(output / "summary.json", summary)
        (output / "REPORT.md").write_text(_report(summary), encoding="utf-8")
    return summary


def m0_evalplus_succeeded(summary: dict[str, Any]) -> bool:
    return bool(
        summary["complete_selection"]
        and summary["expected_outcomes_matched"]
        and summary["semantic_replays_matched"]
        and summary["plus_boundary_catches"] >= 5
    )


def m0_evalplus_selection_succeeded(summary: dict[str, Any]) -> bool:
    """Check a partial selection without falsely calling it the whole M0 gate."""

    return bool(summary["expected_outcomes_matched"] and summary["semantic_replays_matched"])
