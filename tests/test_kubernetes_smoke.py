from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from verirun.artifacts import ArtifactIntegrityError
from verirun.executor import KubernetesJobExecutor, LocalExecutor
from verirun.kubernetes_smoke import (
    _cases,
    _kubectl_json,
    kubernetes_runtime_identity,
    kubernetes_smoke_markdown,
    kubernetes_smoke_succeeded,
    run_kubernetes_smoke,
)
from verirun.models import VerificationStatus


def test_kubernetes_smoke_has_eight_bounded_attack_cases() -> None:
    names = {case.name for case in _cases()}

    assert len(_cases()) == 9
    assert {
        "kubernetes-timeout",
        "kubernetes-output-flood",
        "kubernetes-memory-pressure",
        "kubernetes-no-egress",
        "kubernetes-read-only-root",
        "kubernetes-privilege-escalation",
        "kubernetes-invalid-source",
        "kubernetes-artifact-tamper",
    } <= names


def test_kubernetes_smoke_success_requires_expected_statuses_and_replays() -> None:
    assert kubernetes_smoke_succeeded(
        {"expected_statuses_matched": True, "semantic_replays_matched": True}
    )
    assert not kubernetes_smoke_succeeded(
        {"expected_statuses_matched": False, "semantic_replays_matched": True}
    )


def test_kubernetes_smoke_report_states_claim_and_fork_bomb_boundary() -> None:
    report = kubernetes_smoke_markdown(
        {
            "kubernetes_context": "kind-test",
            "kubernetes_namespace": "verirun-test",
            "kubernetes_runtime_class": "gvisor",
            "kubernetes_runtime_identity": {
                "kubernetes": {"client_git_version": "v1.test", "server_git_version": "v1.test"},
                "runtime_class": {"name": "gvisor", "handler": "runsc"},
                "nodes": [
                    {
                        "name": "kind-test-control-plane",
                        "kubelet_version": "v1.test",
                        "container_runtime_version": "containerd://test",
                        "kernel_version": "test",
                        "os_image": "test",
                        "architecture": "arm64",
                    }
                ],
            },
            "container_image": "example.invalid/python@sha256:" + "a" * 64,
            "environment": {"python": "3.12", "platform": "test"},
            "source": {"revision": "test", "working_tree_clean": True},
            "cases": [
                {
                    "name": "kubernetes-timeout",
                    "expected_status": "timeout",
                    "baseline_status": "timeout",
                    "replay_status": "timeout",
                    "comparison": {"matched": True},
                }
            ],
        }
    )

    assert "does not claim protection" in report
    assert "does not run a destructive fork bomb" in report
    assert "Runtime handler: `runsc`" in report


def test_kubernetes_smoke_writes_replayable_evidence_without_a_cluster(
    monkeypatch, tmp_path: Path
) -> None:
    expected_by_task = {case.task_id: case for case in _cases()}

    def execute_as_expected(
        self: KubernetesJobExecutor, manifest, store, *, attempt_id: str | None = None
    ):
        case = expected_by_task[manifest.candidate.task_id]
        try:
            store.read_bytes(manifest.candidate.source)
        except ArtifactIntegrityError:
            status = VerificationStatus.INFRA_ERROR
            error_class = "artifact_integrity_error"
        else:
            status = case.expected_status
            error_class = None if status is VerificationStatus.PASSED else "expected_failure"
        return LocalExecutor()._result(
            manifest=manifest,
            store=store,
            attempt_id=attempt_id or "fake",
            started_at=datetime.now(UTC),
            started_clock=time.perf_counter(),
            status=status,
            error_class=error_class,
            error_message=None,
            exit_code=0,
            stdout=b"synthetic\n",
            stderr=b"",
            output_truncated=case.expected_output_truncated,
        )

    monkeypatch.setattr(KubernetesJobExecutor, "execute", execute_as_expected)
    monkeypatch.setattr(
        "verirun.kubernetes_smoke.source_state",
        lambda: {"revision": "clean-revision", "working_tree_clean": True, "source": "git"},
    )
    monkeypatch.setattr(
        "verirun.kubernetes_smoke.kubernetes_runtime_identity",
        lambda _context, _runtime_class: {
            "kubernetes": {"client_git_version": "v1.test", "server_git_version": "v1.test"},
            "runtime_class": {"name": "gvisor", "handler": "runsc"},
            "nodes": [{"name": "kind-test-control-plane"}],
        },
    )

    summary = run_kubernetes_smoke(
        tmp_path,
        image="example.invalid/python@sha256:" + "a" * 64,
        context="kind-test",
        namespace="verirun-test",
        runtime_class="gvisor",
    )

    assert kubernetes_smoke_succeeded(summary)
    assert summary["source"] == {
        "revision": "clean-revision",
        "working_tree_clean": True,
        "source": "git",
    }
    assert summary["kubernetes_runtime_identity"]["runtime_class"]["handler"] == "runsc"
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "REPORT.md").is_file()
    assert (tmp_path / "cases" / "kubernetes-artifact-tamper" / "comparison.json").is_file()


def test_kubernetes_runtime_identity_reads_version_runtime_class_and_nodes(
    monkeypatch,
) -> None:
    payloads = [
        {"clientVersion": {"gitVersion": "v1.37.0"}, "serverVersion": {"gitVersion": "v1.37.0"}},
        {"handler": "runsc"},
        {
            "items": [
                {
                    "metadata": {"name": "kind-test-control-plane"},
                    "status": {
                        "nodeInfo": {
                            "kubeletVersion": "v1.37.0",
                            "containerRuntimeVersion": "containerd://2.3.4",
                            "kernelVersion": "6.6",
                            "osImage": "Debian",
                            "architecture": "arm64",
                        }
                    },
                }
            ]
        },
    ]

    monkeypatch.setattr("verirun.kubernetes_smoke._kubectl_json", lambda *_args: payloads.pop(0))

    assert kubernetes_runtime_identity("kind-test", "gvisor") == {
        "kubernetes": {"client_git_version": "v1.37.0", "server_git_version": "v1.37.0"},
        "runtime_class": {"name": "gvisor", "handler": "runsc"},
        "nodes": [
            {
                "name": "kind-test-control-plane",
                "kubelet_version": "v1.37.0",
                "container_runtime_version": "containerd://2.3.4",
                "kernel_version": "6.6",
                "os_image": "Debian",
                "architecture": "arm64",
            }
        ],
    }


def test_kubectl_json_returns_a_json_object_without_a_cluster(monkeypatch) -> None:
    def run(command, **_kwargs):
        assert command == ["kubectl", "--context", "kind-test", "version", "--output=json"]
        return subprocess.CompletedProcess(command, 0, stdout=b'{"ok": true}', stderr=b"")

    monkeypatch.setattr("verirun.kubernetes_smoke.subprocess.run", run)

    assert _kubectl_json("kind-test", ("version", "--output=json")) == {"ok": True}
