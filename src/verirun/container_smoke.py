"""Reproducible development-container runtime evidence for v0.3."""

from __future__ import annotations

import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from verirun.artifacts import ArtifactStore
from verirun.canonical import write_canonical_json
from verirun.executor import ContainerExecutor
from verirun.fixtures import SmokeCase, build_smoke_manifest
from verirun.models import EvalManifest, ExecutionSpec, VerificationStatus
from verirun.provenance import source_state
from verirun.replay import compare_results


def _cases() -> tuple[tuple[SmokeCase, float], ...]:
    return (
        (
            SmokeCase(
                name="container-pass",
                task_id="Synthetic/ContainerPass",
                candidate="def answer() -> int:\n    return 42\n",
                tests="assert answer() == 42\n",
                expected_status=VerificationStatus.PASSED,
            ),
            5.0,
        ),
        (
            SmokeCase(
                name="container-timeout",
                task_id="Synthetic/ContainerTimeout",
                candidate="def wait() -> None:\n    while True:\n        pass\n",
                tests="wait()\n",
                expected_status=VerificationStatus.TIMEOUT,
            ),
            1.0,
        ),
        (
            SmokeCase(
                name="container-read-only",
                task_id="Synthetic/ContainerReadOnly",
                candidate=(
                    "def write_workdir() -> None:\n"
                    "    open('/work/blocked.txt', 'w').write('blocked')\n"
                ),
                tests="write_workdir()\n",
                expected_status=VerificationStatus.TEST_FAILURE,
            ),
            5.0,
        ),
        (
            SmokeCase(
                name="container-no-network",
                task_id="Synthetic/ContainerNoNetwork",
                candidate=(
                    "import socket\n\n"
                    "def egress() -> None:\n"
                    "    socket.create_connection(('198.51.100.1', 80), timeout=0.2)\n"
                ),
                tests="egress()\n",
                expected_status=VerificationStatus.TEST_FAILURE,
            ),
            5.0,
        ),
    )


def _manifest(
    case: SmokeCase, timeout_seconds: float, image: str, store: ArtifactStore
) -> EvalManifest:
    baseline = build_smoke_manifest(case, store)
    return baseline.model_copy(
        update={
            "verifier": baseline.verifier.model_copy(
                update={
                    "image_digest": image.rsplit("@", maxsplit=1)[1],
                    "timeout_seconds": timeout_seconds,
                }
            ),
            "execution": ExecutionSpec(
                engine="container",
                sandbox_policy="development-container",
                container_image=image,
                container_cpus=1.0,
                container_memory_mb=256,
                container_pids_limit=64,
            ),
        }
    )


def container_smoke_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# VeriRun v0.3 Development-Container Smoke Report",
        "",
        "> This report exercises a local Docker-compatible development container. It is",
        "> not Linux/Kubernetes/gVisor security evidence or a production sandbox claim.",
        "",
        f"- Python: `{summary['environment']['python']}`",
        f"- Platform: `{summary['environment']['platform']}`",
        f"- Image: `{summary['container_image']}`",
        f"- Source revision: `{summary['source']['revision']}`",
        f"- Working tree clean at start: `{summary['source']['working_tree_clean']}`",
        "",
        "| Case | Expected | Baseline | Replay | Semantic match |",
        "|---|---|---|---|---|",
    ]
    for row in summary["cases"]:
        lines.append(
            "| {name} | {expected} | {baseline} | {replay} | {matched} |".format(
                name=row["name"],
                expected=row["expected_status"],
                baseline=row["baseline_status"],
                replay=row["replay_status"],
                matched="yes" if row["comparison"]["matched"] else "no",
            )
        )
    lines.extend(
        [
            "",
            "## What this exercises",
            "",
            "- a digest-pinned Python image and the declared CPU/memory/PID limits;",
            "- container-level timeout kill and forced removal;",
            "- read-only `/work` behavior; and",
            "- `--network none` egress denial.",
            "",
            "## Limitations",
            "",
            "- The Docker daemon, image, host kernel, and file-sharing configuration remain",
            "  trusted in this development tier.",
            "- PID and memory enforcement have command-contract regressions but require the",
            "  future Linux/Kubernetes runtime evidence for a stronger claim.",
            "",
        ]
    )
    return "\n".join(lines)


def run_container_smoke(output: Path, *, image: str) -> dict[str, Any]:
    source = source_state()
    output.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(output / "artifacts")
    executor = ContainerExecutor()
    cases: list[dict[str, Any]] = []

    for case, timeout_seconds in _cases():
        manifest = _manifest(case, timeout_seconds, image, store)
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
        "schema_version": "verirun.container-smoke-report/v1",
        "generated_at": datetime.now(UTC),
        "container_image": image,
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
    (output / "REPORT.md").write_text(container_smoke_markdown(summary), encoding="utf-8")
    return summary


def container_smoke_succeeded(summary: dict[str, Any]) -> bool:
    return bool(summary["expected_statuses_matched"] and summary["semantic_replays_matched"])
