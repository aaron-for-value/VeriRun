"""End-to-end v0.1 synthetic smoke workflow."""

from __future__ import annotations

import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from verirun.artifacts import ArtifactStore
from verirun.canonical import write_canonical_json
from verirun.executor import LocalExecutor
from verirun.fixtures import build_smoke_manifest, smoke_cases
from verirun.provenance import source_state
from verirun.replay import compare_results
from verirun.report import smoke_markdown


def run_smoke(output: Path) -> dict[str, Any]:
    source = source_state()
    output.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(output / "artifacts")
    executor = LocalExecutor()
    cases: list[dict[str, Any]] = []

    for case in smoke_cases():
        manifest = build_smoke_manifest(case, store)
        baseline = executor.execute(manifest, store, attempt_id=f"{case.name}-baseline")
        replay = executor.execute(manifest, store, attempt_id=f"{case.name}-replay")
        comparison = compare_results(baseline, replay)
        case_directory = output / "cases" / case.name
        write_canonical_json(case_directory / "manifest.json", manifest)
        write_canonical_json(case_directory / "baseline.json", baseline)
        write_canonical_json(case_directory / "replay.json", replay)
        write_canonical_json(case_directory / "comparison.json", comparison)
        cases.append(
            {
                "name": case.name,
                "expected_status": case.expected_status.value,
                "baseline_status": baseline.status.value,
                "replay_status": replay.status.value,
                "expected_matched": baseline.status is case.expected_status
                and replay.status is case.expected_status,
                "comparison": comparison.model_dump(mode="json"),
            }
        )

    summary: dict[str, Any] = {
        "schema_version": "verirun.smoke-report/v1",
        "generated_at": datetime.now(UTC),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "implementation": platform.python_implementation(),
        },
        "source": source,
        "expected_statuses_matched": all(item["expected_matched"] for item in cases),
        "semantic_replays_matched": all(item["comparison"]["matched"] for item in cases),
        "cases": cases,
    }
    write_canonical_json(output / "summary.json", summary)
    (output / "REPORT.md").write_text(smoke_markdown(summary), encoding="utf-8")
    return summary


def smoke_succeeded(summary: dict[str, Any]) -> bool:
    return bool(summary["expected_statuses_matched"] and summary["semantic_replays_matched"])


def main() -> int:
    summary = run_smoke(Path("evidence/v0.1/synthetic"))
    return 0 if smoke_succeeded(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
