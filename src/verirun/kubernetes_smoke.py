"""Reproducible Kubernetes/gVisor runtime evidence for v0.3."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from verirun.artifacts import ArtifactStore
from verirun.canonical import write_canonical_json
from verirun.executor import KubernetesJobExecutor
from verirun.fixtures import SmokeCase, build_smoke_manifest
from verirun.models import EvalManifest, ExecutionSpec, VerificationStatus
from verirun.provenance import source_state
from verirun.replay import compare_results


@dataclass(frozen=True)
class KubernetesSmokeCase:
    """One bounded runtime attack probe and its expected classified result."""

    name: str
    task_id: str
    candidate: str
    tests: str
    expected_status: VerificationStatus
    timeout_seconds: float = 15.0
    memory_mb: int = 128
    max_output_bytes: int = 4096
    expected_output_truncated: bool = False
    tamper_candidate_artifact: bool = False


def _cases() -> tuple[KubernetesSmokeCase, ...]:
    return (
        KubernetesSmokeCase(
            name="kubernetes-pass-control",
            task_id="Synthetic/KubernetesPass",
            candidate="def answer() -> int:\n    return 42\n",
            tests="assert answer() == 42\n",
            expected_status=VerificationStatus.PASSED,
        ),
        KubernetesSmokeCase(
            name="kubernetes-timeout",
            task_id="Synthetic/KubernetesTimeout",
            candidate="def never_returns() -> None:\n    while True:\n        pass\n",
            tests="never_returns()\n",
            expected_status=VerificationStatus.TIMEOUT,
            timeout_seconds=1.0,
        ),
        KubernetesSmokeCase(
            name="kubernetes-output-flood",
            task_id="Synthetic/KubernetesOutputFlood",
            candidate="def flood() -> None:\n    print('x' * 8192)\n",
            tests="flood()\n",
            expected_status=VerificationStatus.PASSED,
            timeout_seconds=15.0,
            max_output_bytes=512,
            expected_output_truncated=True,
        ),
        KubernetesSmokeCase(
            name="kubernetes-memory-pressure",
            task_id="Synthetic/KubernetesMemoryPressure",
            candidate=(
                "def exhaust_memory() -> None:\n"
                "    blocks = []\n"
                "    while True:\n"
                "        blocks.append(b'x' * (1024 * 1024))\n"
            ),
            tests="exhaust_memory()\n",
            expected_status=VerificationStatus.OOM,
            timeout_seconds=20.0,
            memory_mb=64,
        ),
        KubernetesSmokeCase(
            name="kubernetes-no-egress",
            task_id="Synthetic/KubernetesNoEgress",
            candidate=(
                "import socket\n\n"
                "def exfiltrate() -> None:\n"
                "    socket.create_connection(('198.51.100.1', 80), timeout=0.2)\n"
            ),
            tests="exfiltrate()\n",
            expected_status=VerificationStatus.TEST_FAILURE,
        ),
        KubernetesSmokeCase(
            name="kubernetes-read-only-root",
            task_id="Synthetic/KubernetesReadOnlyRoot",
            candidate=(
                "def write_root() -> None:\n"
                "    open('/verirun-blocked.txt', 'w').write('blocked')\n"
            ),
            tests="write_root()\n",
            expected_status=VerificationStatus.TEST_FAILURE,
        ),
        KubernetesSmokeCase(
            name="kubernetes-privilege-escalation",
            task_id="Synthetic/KubernetesPrivilegeEscalation",
            candidate=("import os\n\ndef become_root() -> None:\n    os.setuid(0)\n"),
            tests="become_root()\n",
            expected_status=VerificationStatus.TEST_FAILURE,
        ),
        KubernetesSmokeCase(
            name="kubernetes-invalid-source",
            task_id="Synthetic/KubernetesInvalidSource",
            candidate="def broken(:\n",
            tests="pass\n",
            expected_status=VerificationStatus.COMPILE_ERROR,
        ),
        KubernetesSmokeCase(
            name="kubernetes-artifact-tamper",
            task_id="Synthetic/KubernetesArtifactTamper",
            candidate="def answer() -> int:\n    return 42\n",
            tests="assert answer() == 42\n",
            expected_status=VerificationStatus.INFRA_ERROR,
            tamper_candidate_artifact=True,
        ),
    )


def _manifest(
    case: KubernetesSmokeCase,
    *,
    image: str,
    context: str,
    namespace: str,
    runtime_class: str,
    store: ArtifactStore,
) -> EvalManifest:
    baseline = build_smoke_manifest(
        SmokeCase(
            name=case.name,
            task_id=case.task_id,
            candidate=case.candidate,
            tests=case.tests,
            expected_status=case.expected_status,
        ),
        store,
    )
    return baseline.model_copy(
        update={
            "verifier": baseline.verifier.model_copy(
                update={
                    "image_digest": image.rsplit("@", maxsplit=1)[1],
                    "timeout_seconds": case.timeout_seconds,
                    "max_output_bytes": case.max_output_bytes,
                }
            ),
            "execution": ExecutionSpec(
                engine="kubernetes",
                sandbox_policy="kubernetes-gvisor",
                container_image=image,
                container_cpus=0.25,
                container_memory_mb=case.memory_mb,
                kubernetes_context=context,
                kubernetes_namespace=namespace,
                kubernetes_runtime_class=runtime_class,
            ),
        }
    )


def kubernetes_smoke_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# VeriRun v0.3 Kubernetes/gVisor Smoke Report",
        "",
        "> This report records bounded Jobs in the declared Kubernetes context and",
        "> RuntimeClass. It does not claim protection from runtime, kernel, image, CNI,",
        "> control-plane, or operator compromise outside the tested controls.",
        "",
        f"- Kubernetes context: `{summary['kubernetes_context']}`",
        f"- Namespace: `{summary['kubernetes_namespace']}`",
        f"- RuntimeClass: `{summary['kubernetes_runtime_class']}`",
        f"- Image: `{summary['container_image']}`",
        f"- Python: `{summary['environment']['python']}`",
        f"- Platform: `{summary['environment']['platform']}`",
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
            "## Attack coverage",
            "",
            "- timeout, bounded output, memory pressure, and default-deny egress;",
            "- root filesystem write and in-process privilege-escalation attempts;",
            "- invalid source rejection before execution; and",
            "- content-addressed candidate artifact tamper detection.",
            "",
            "Each case is executed twice. Created Jobs are deleted by the executor; the",
            "operator-provisioned namespace and its `default-deny-egress` policy remain",
            "for the duration of the report.",
            "",
            "## Residual risks",
            "",
            "- Kubernetes has no portable per-Pod PID limit in this contract, so this report",
            "  deliberately does not run a destructive fork bomb.",
            "- Presence of `default-deny-egress` is preflighted; CNI enforcement is tested",
            "  only by this report's bounded connection attempt.",
            "",
        ]
    )
    return "\n".join(lines)


def run_kubernetes_smoke(
    output: Path,
    *,
    image: str,
    context: str,
    namespace: str,
    runtime_class: str,
) -> dict[str, Any]:
    """Execute the v0.3 Kubernetes attack matrix twice and write evidence."""

    source = source_state()
    output.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(output / "artifacts")
    executor = KubernetesJobExecutor()
    cases: list[dict[str, Any]] = []

    for case in _cases():
        manifest = _manifest(
            case,
            image=image,
            context=context,
            namespace=namespace,
            runtime_class=runtime_class,
            store=store,
        )
        if case.tamper_candidate_artifact:
            manifest = manifest.model_copy(
                update={
                    "candidate": manifest.candidate.model_copy(
                        update={
                            "source": manifest.candidate.source.model_copy(
                                update={"sha256": "0" * 64}
                            )
                        }
                    )
                }
            )
        baseline = executor.execute(manifest, store, attempt_id=f"{case.name}-baseline")
        replay = executor.execute(manifest, store, attempt_id=f"{case.name}-replay")
        comparison = compare_results(baseline, replay)
        case_directory = output / "cases" / case.name
        write_canonical_json(case_directory / "manifest.json", manifest)
        write_canonical_json(case_directory / "baseline.json", baseline)
        write_canonical_json(case_directory / "replay.json", replay)
        write_canonical_json(case_directory / "comparison.json", comparison)
        expected_matched = (
            baseline.status is case.expected_status
            and replay.status is case.expected_status
            and baseline.output_truncated is case.expected_output_truncated
            and replay.output_truncated is case.expected_output_truncated
        )
        cases.append(
            {
                "name": case.name,
                "expected_status": case.expected_status.value,
                "baseline_status": baseline.status.value,
                "replay_status": replay.status.value,
                "expected_output_truncated": case.expected_output_truncated,
                "baseline_output_truncated": baseline.output_truncated,
                "replay_output_truncated": replay.output_truncated,
                "expected_matched": expected_matched,
                "comparison": comparison.model_dump(mode="json"),
            }
        )

    summary: dict[str, Any] = {
        "schema_version": "verirun.kubernetes-smoke-report/v1",
        "generated_at": datetime.now(UTC),
        "container_image": image,
        "kubernetes_context": context,
        "kubernetes_namespace": namespace,
        "kubernetes_runtime_class": runtime_class,
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
    (output / "REPORT.md").write_text(kubernetes_smoke_markdown(summary), encoding="utf-8")
    return summary


def kubernetes_smoke_succeeded(summary: dict[str, Any]) -> bool:
    return bool(summary["expected_statuses_matched"] and summary["semantic_replays_matched"])
