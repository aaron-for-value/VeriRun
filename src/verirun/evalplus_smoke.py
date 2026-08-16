"""Real EvalPlus subset smoke workflow for v0.1 compatibility evidence."""

from __future__ import annotations

import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from verirun.adapters.evalplus import (
    EvalPlusTaskResult,
    evaluate_candidate,
    load_subset,
    obvious_failure_solution,
    oracle_solution,
)
from verirun.canonical import content_hash, write_canonical_json
from verirun.models import VerificationStatus
from verirun.provenance import source_state

DEFAULT_TASK_IDS = ("HumanEval/0", "HumanEval/1", "HumanEval/2")
SUBSET_LABEL = "verirun-v0.1-humaneval-plus-smoke-3"


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# VeriRun v0.1 EvalPlus compatibility smoke",
        "",
        "> This is a labeled three-task compatibility smoke, not a HumanEval+ score,",
        "> leaderboard result, or hostile-code sandbox claim.",
        "",
        f"- EvalPlus package: `{summary['package_release']}`",
        f"- Dataset release: `{summary['dataset_release']}`",
        f"- Dataset digest: `{summary['dataset_digest']}`",
        f"- Subset: `{summary['subset_label']}`",
        f"- Subset digest: `{summary['subset_digest']}`",
        f"- Source revision: `{summary['source']['revision']}`",
        f"- Working tree clean at start: `{summary['source']['working_tree_clean']}`",
        f"- EvalPlus max memory bytes: `{summary['environment']['evalplus_max_memory_bytes']}`",
        f"- Expected outcomes matched: `{str(summary['expected_outcomes_matched']).lower()}`",
        f"- Semantic replays matched: `{str(summary['semantic_replays_matched']).lower()}`",
        "",
        "| Task | Candidate recipe | Base | Plus | Mapped | Replay |",
        "|---|---|---|---|---|---|",
    ]
    for case in summary["cases"]:
        lines.append(
            f"| `{case['task_id']}` | `{case['recipe']}` | `{case['base_status']}` | "
            f"`{case['plus_status']}` | `{case['mapped_status']}` | "
            f"`{str(case['replay_matched']).lower()}` |"
        )
    lines.extend(
        [
            "",
            "Candidates are recreated deterministically from the pinned dataset: the oracle recipe",
            "uses `prompt + canonical_solution`; the failure recipe uses `prompt + pass`.",
            "Candidate source is not copied into this report; its SHA-256 identity is present",
            "in each result.",
            "",
        ]
    )
    return "\n".join(lines)


def _case_record(
    result: EvalPlusTaskResult,
    replay: EvalPlusTaskResult,
    *,
    recipe: str,
    expected_pass: bool,
) -> dict[str, Any]:
    replay_matched = content_hash(result) == content_hash(replay)
    actual_pass = result.mapped_status is VerificationStatus.PASSED
    return {
        "task_id": result.task_id,
        "candidate_id": result.candidate_id,
        "candidate_hash": result.candidate_hash,
        "recipe": recipe,
        "base_status": result.base.status,
        "plus_status": result.plus.status,
        "mapped_status": result.mapped_status.value,
        "expected_pass": expected_pass,
        "expected_matched": actual_pass is expected_pass,
        "replay_matched": replay_matched,
        "result_hash": content_hash(result),
        "replay_hash": content_hash(replay),
    }


def run_evalplus_smoke(
    output: Path,
    *,
    task_ids: tuple[str, ...] = DEFAULT_TASK_IDS,
) -> dict[str, Any]:
    source = source_state()
    output.mkdir(parents=True, exist_ok=True)
    subset = load_subset("humaneval", task_ids, subset_label=SUBSET_LABEL)
    cases: list[dict[str, Any]] = []

    for task_id in subset.task_ids:
        task_directory = output / "cases" / task_id.replace("/", "_")
        for recipe, factory, expected_pass in (
            ("prompt+canonical_solution", oracle_solution, True),
            ("prompt+pass", obvious_failure_solution, False),
        ):
            candidate_id = f"{task_id}:{recipe}"
            solution = factory(subset, task_id)
            result = evaluate_candidate(
                subset,
                task_id=task_id,
                candidate_id=candidate_id,
                solution=solution,
            )
            replay = evaluate_candidate(
                subset,
                task_id=task_id,
                candidate_id=candidate_id,
                solution=solution,
            )
            record = _case_record(
                result,
                replay,
                recipe=recipe,
                expected_pass=expected_pass,
            )
            write_canonical_json(task_directory / f"{recipe}-baseline.json", result)
            write_canonical_json(task_directory / f"{recipe}-replay.json", replay)
            cases.append(record)

    summary: dict[str, Any] = {
        "schema_version": "verirun.evalplus-smoke-report/v1",
        "generated_at": datetime.now(UTC),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "implementation": platform.python_implementation(),
            "evalplus_max_memory_bytes": os.environ.get(
                "EVALPLUS_MAX_MEMORY_BYTES", "upstream-default"
            ),
        },
        "source": source,
        "package_release": "v0.3.1",
        "dataset_release": subset.dataset_release,
        "dataset_digest": subset.dataset_digest,
        "subset_label": subset.subset_label,
        "subset_digest": subset.subset_digest,
        "task_ids": subset.task_ids,
        "expected_outcomes_matched": all(case["expected_matched"] for case in cases),
        "semantic_replays_matched": all(case["replay_matched"] for case in cases),
        "cases": cases,
    }
    write_canonical_json(output / "summary.json", summary)
    (output / "REPORT.md").write_text(_report(summary), encoding="utf-8")
    return summary


def evalplus_smoke_succeeded(summary: dict[str, Any]) -> bool:
    return bool(summary["expected_outcomes_matched"] and summary["semantic_replays_matched"])
