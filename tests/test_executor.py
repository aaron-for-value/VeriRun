from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from verirun.artifacts import ArtifactStore
from verirun.executor import (
    ContainerExecutor,
    KubernetesJobExecutor,
    LocalExecutor,
    _container_workspace_directory,
    executor_for,
)
from verirun.fixtures import SmokeCase, build_smoke_manifest, smoke_cases
from verirun.models import ExecutionSpec, VerificationStatus


def _container_manifest(case: SmokeCase, store: ArtifactStore):
    manifest = build_smoke_manifest(case, store)
    image = f"example.invalid/verirun@sha256:{'d' * 64}"
    return manifest.model_copy(
        update={
            "verifier": manifest.verifier.model_copy(
                update={"image_digest": image.rsplit("@", maxsplit=1)[1]}
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


def _kubernetes_manifest(case: SmokeCase, store: ArtifactStore):
    manifest = build_smoke_manifest(case, store)
    image = f"example.invalid/verirun@sha256:{'f' * 64}"
    return manifest.model_copy(
        update={
            "verifier": manifest.verifier.model_copy(
                update={"image_digest": image.rsplit("@", maxsplit=1)[1]}
            ),
            "execution": ExecutionSpec(
                engine="kubernetes",
                sandbox_policy="kubernetes-gvisor",
                container_image=image,
                container_cpus=1.0,
                container_memory_mb=256,
                kubernetes_context="test-context",
                kubernetes_namespace="test-namespace",
                kubernetes_runtime_class="gvisor",
            ),
        }
    )


@pytest.mark.parametrize("case", smoke_cases(), ids=lambda case: case.name)
def test_smoke_case_classification(case: SmokeCase, tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(case, store)

    result = LocalExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is case.expected_status
    assert result.candidate_hash == manifest.candidate.source.sha256
    assert result.test_hash == manifest.verifier.tests.sha256
    store.verify(result.stdout)
    store.verify(result.stderr)


def test_output_is_truncated(tmp_path: Path) -> None:
    case = SmokeCase(
        name="output",
        task_id="Synthetic/Output",
        candidate="print('x' * 1000)\ndef answer():\n    return 42\n",
        tests="assert answer() == 42\n",
        expected_status=VerificationStatus.PASSED,
    )
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(case, store)
    manifest = manifest.model_copy(
        update={"verifier": manifest.verifier.model_copy(update={"max_output_bytes": 128})}
    )

    result = LocalExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.PASSED
    assert result.output_truncated is True
    assert len(store.read_bytes(result.stdout)) <= 128


def test_artifact_tampering_becomes_infra_error(tmp_path: Path) -> None:
    case = smoke_cases()[0]
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(case, store)
    (tmp_path / manifest.candidate.source.relative_path).write_text("tampered", encoding="utf-8")

    result = LocalExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == "artifact_integrity_error"


def test_verifier_syntax_error_is_infrastructure_failure(tmp_path: Path) -> None:
    case = SmokeCase(
        name="bad-verifier",
        task_id="Synthetic/BadVerifier",
        candidate="def answer():\n    return 42\n",
        tests="assert answer(\n",
        expected_status=VerificationStatus.INFRA_ERROR,
    )
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(case, store)

    result = LocalExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == "verifier_syntax_error"


def test_candidate_runtime_error_is_test_failure(tmp_path: Path) -> None:
    case = SmokeCase(
        name="runtime-error",
        task_id="Synthetic/RuntimeError",
        candidate="def answer():\n    raise ValueError('broken')\n",
        tests="answer()\n",
        expected_status=VerificationStatus.TEST_FAILURE,
    )
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(case, store)

    result = LocalExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.TEST_FAILURE
    assert result.error_class == "runtime_error"
    assert result.error_message == "ValueError:broken"


@pytest.mark.parametrize(
    ("candidate", "expected_class"),
    [
        ("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n", "process_signal"),
        ("import os\nos._exit(7)\n", "unknown_exit_code"),
    ],
)
def test_abnormal_runner_exit_is_infrastructure_failure(
    tmp_path: Path,
    candidate: str,
    expected_class: str,
) -> None:
    case = SmokeCase(
        name=expected_class,
        task_id=f"Synthetic/{expected_class}",
        candidate=candidate,
        tests="pass\n",
        expected_status=VerificationStatus.INFRA_ERROR,
    )
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(case, store)

    result = LocalExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == expected_class


def test_process_start_error_is_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    case = smoke_cases()[0]
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(case, store)

    def fail_to_start(*_args: object, **_kwargs: object) -> None:
        raise OSError("cannot start")

    monkeypatch.setattr("verirun.executor.subprocess.Popen", fail_to_start)
    result = LocalExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == "process_start_error"


def test_container_command_enforces_development_limits(tmp_path: Path) -> None:
    command = ContainerExecutor(runtime_binary="docker-test")._container_command(
        directory=tmp_path,
        container_name="verirun-test",
        image=f"example.invalid/verirun@sha256:{'e' * 64}",
        cpus=1.0,
        memory_mb=256,
        pids_limit=64,
    )

    assert command[:4] == ["docker-test", "run", "--pull=never", "--name"]
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert "--user" in command and command[command.index("--user") + 1] == "65534:65534"
    assert "--cap-drop" in command and command[command.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in command
    assert "no-new-privileges" in command
    assert "--pids-limit" in command and command[command.index("--pids-limit") + 1] == "64"
    assert "--memory" in command and command[command.index("--memory") + 1] == "256m"
    assert "--cpus" in command and command[command.index("--cpus") + 1] == "1.0"
    assert any(item.endswith(",dst=/work,readonly") for item in command)


def test_container_runner_directory_uses_the_current_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    with tempfile.TemporaryDirectory(
        prefix=".verirun-container-", dir=_container_workspace_directory()
    ) as directory:
        assert Path(directory).parent == tmp_path.resolve()


def test_container_timeout_tolerates_host_process_group_permission_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeProcess:
        pid = 123
        returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            if timeout is not None:
                raise subprocess.TimeoutExpired("docker run", timeout)
            return b"", b""

    def fake_maintenance(self: ContainerExecutor, arguments: tuple[str, ...]):
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    store = ArtifactStore(tmp_path)
    manifest = _container_manifest(smoke_cases()[-1], store)
    monkeypatch.setattr(
        "verirun.executor.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess()
    )
    monkeypatch.setattr(ContainerExecutor, "_maintenance", fake_maintenance)

    def deny_killpg(*_args: object) -> None:
        raise PermissionError

    monkeypatch.setattr("verirun.executor.os.killpg", deny_killpg)

    result = ContainerExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.TIMEOUT


def test_container_executor_rejects_a_local_manifest(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(smoke_cases()[0], store)

    result = ContainerExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.POLICY_VIOLATION
    assert result.error_class == "execution_tier_mismatch"


def test_container_start_error_is_an_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_to_start(*_args: object, **_kwargs: object) -> None:
        raise OSError("docker unavailable")

    store = ArtifactStore(tmp_path)
    manifest = _container_manifest(smoke_cases()[0], store)
    monkeypatch.setattr("verirun.executor.subprocess.Popen", fail_to_start)

    result = ContainerExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == "container_start_error"


def test_container_executor_rejects_mismatched_image_identity(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    container_manifest = _container_manifest(smoke_cases()[0], store)
    manifest = container_manifest.model_copy(
        update={"verifier": container_manifest.verifier.model_copy(update={"image_digest": None})}
    )

    result = ContainerExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.POLICY_VIOLATION
    assert result.error_class == "container_image_identity_mismatch"


def test_container_executor_maps_reported_oom_and_removes_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        pid = 1234
        returncode = 137

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            return b"", b""

    maintenance_calls: list[tuple[str, ...]] = []

    def fake_maintenance(self: ContainerExecutor, arguments: tuple[str, ...]):
        maintenance_calls.append(arguments)
        if arguments[0] == "inspect":
            return subprocess.CompletedProcess([], 0, stdout=b"true\n", stderr=b"")
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    case = smoke_cases()[0]
    store = ArtifactStore(tmp_path)
    manifest = _container_manifest(case, store)
    monkeypatch.setattr(
        "verirun.executor.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess()
    )
    monkeypatch.setattr(ContainerExecutor, "_maintenance", fake_maintenance)

    result = ContainerExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.OOM
    assert result.error_class == "memory_limit_exceeded"
    assert maintenance_calls[0][0] == "inspect"
    assert maintenance_calls[1][0] == "rm"


def test_container_cleanup_failure_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        pid = 1234
        returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            return b"ok", b""

    def fake_maintenance(self: ContainerExecutor, arguments: tuple[str, ...]):
        assert arguments[0] == "rm"
        return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"daemon unavailable")

    case = smoke_cases()[0]
    store = ArtifactStore(tmp_path)
    manifest = _container_manifest(case, store)
    monkeypatch.setattr(
        "verirun.executor.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess()
    )
    monkeypatch.setattr(ContainerExecutor, "_maintenance", fake_maintenance)

    result = ContainerExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == "container_cleanup_error"


def test_container_timeout_kills_and_removes_named_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        pid = 1234
        returncode = 137
        calls = 0

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("docker run", timeout)
            return b"", b""

    maintenance_calls: list[tuple[str, ...]] = []

    def fake_maintenance(self: ContainerExecutor, arguments: tuple[str, ...]):
        maintenance_calls.append(arguments)
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    case = smoke_cases()[0]
    store = ArtifactStore(tmp_path)
    manifest = _container_manifest(case, store)
    monkeypatch.setattr(
        "verirun.executor.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess()
    )
    monkeypatch.setattr(ContainerExecutor, "_maintenance", fake_maintenance)

    result = ContainerExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.TIMEOUT
    assert result.error_class == "wall_time_exceeded"
    assert [call[0] for call in maintenance_calls] == ["kill", "rm"]


@pytest.mark.parametrize(
    ("returncode", "stderr", "expected_status", "expected_error_class"),
    [
        (0, b"", VerificationStatus.PASSED, None),
        (
            10,
            b"VERIRUN:assertion_failure:wrong answer\n",
            VerificationStatus.TEST_FAILURE,
            "assertion_failure",
        ),
        (
            11,
            b"VERIRUN:runtime_error:ValueError:broken\n",
            VerificationStatus.TEST_FAILURE,
            "runtime_error",
        ),
        (9, b"", VerificationStatus.INFRA_ERROR, "container_runtime_error"),
    ],
)
def test_container_runtime_exit_classification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    stderr: bytes,
    expected_status: VerificationStatus,
    expected_error_class: str | None,
) -> None:
    class FakeProcess:
        pid = 1234

        def __init__(self) -> None:
            self.returncode = returncode

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            return b"", stderr

    def fake_maintenance(self: ContainerExecutor, arguments: tuple[str, ...]):
        assert arguments[0] == "rm"
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    store = ArtifactStore(tmp_path)
    manifest = _container_manifest(smoke_cases()[0], store)
    monkeypatch.setattr(
        "verirun.executor.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess()
    )
    monkeypatch.setattr(ContainerExecutor, "_maintenance", fake_maintenance)

    result = ContainerExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is expected_status
    assert result.error_class == expected_error_class


def test_executor_selection_follows_frozen_execution_tier(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    local_manifest = build_smoke_manifest(smoke_cases()[0], store)
    container_manifest = _container_manifest(smoke_cases()[0], store)
    kubernetes_manifest = _kubernetes_manifest(smoke_cases()[0], store)

    assert isinstance(executor_for(local_manifest), LocalExecutor)
    assert isinstance(executor_for(container_manifest), ContainerExecutor)
    assert isinstance(executor_for(kubernetes_manifest), KubernetesJobExecutor)


def test_kubernetes_job_contract_is_restricted_and_single_attempt() -> None:
    job = KubernetesJobExecutor._job_document(
        job_name="verirun-test",
        namespace="verirun-m2",
        runtime_class="gvisor",
        image=f"example.invalid/verirun@sha256:{'a' * 64}",
        cpus=0.25,
        memory_mb=128,
        timeout_seconds=1.1,
        runner="print('ok')",
    )

    spec = job["spec"]
    assert isinstance(spec, dict)
    assert spec["backoffLimit"] == 0
    assert spec["completions"] == 1
    assert spec["parallelism"] == 1
    template = spec["template"]
    assert isinstance(template, dict)
    pod_spec = template["spec"]
    assert isinstance(pod_spec, dict)
    assert pod_spec["runtimeClassName"] == "gvisor"
    assert pod_spec["automountServiceAccountToken"] is False
    container = pod_spec["containers"][0]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["resources"]["limits"] == {"cpu": "0.25", "memory": "128Mi"}


def test_kubernetes_executor_collects_logs_and_deletes_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_kubectl(
        self: KubernetesJobExecutor,
        _context: str,
        _namespace: str,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 45,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        if arguments[:3] == ("get", "networkpolicy", "default-deny-egress"):
            return subprocess.CompletedProcess([], 0, stdout=b"policy", stderr=b"")
        if arguments[:2] == ("get", "pods"):
            payload = {
                "items": [
                    {"status": {"containerStatuses": [{"state": {"terminated": {"exitCode": 0}}}]}}
                ]
            }
            return subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(payload).encode(), stderr=b""
            )
        if arguments[0] == "logs":
            return subprocess.CompletedProcess([], 0, stdout=b"verified\n", stderr=b"")
        if arguments[0] == "apply":
            assert input_bytes is not None
            assert json.loads(input_bytes)["spec"]["backoffLimit"] == 0
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    monkeypatch.setattr(KubernetesJobExecutor, "_kubectl", fake_kubectl)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.PASSED
    assert store.read_bytes(result.stdout) == b"verified\n"
    assert calls[0] == ("get", "networkpolicy", "default-deny-egress")
    assert calls[-1][0] == "delete"


def test_kubernetes_executor_rejects_missing_default_deny_network_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_kubectl(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"not found")

    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    monkeypatch.setattr(KubernetesJobExecutor, "_kubectl", fake_kubectl)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.POLICY_VIOLATION
    assert result.error_class == "default_deny_egress_missing"


def test_kubernetes_executor_rejects_unavailable_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_kubectl(
        self: KubernetesJobExecutor,
        _context: str,
        _namespace: str,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 45,
    ) -> subprocess.CompletedProcess[bytes]:
        if arguments[:3] == ("get", "networkpolicy", "default-deny-egress"):
            return subprocess.CompletedProcess([], 0, stdout=b"policy", stderr=b"")
        if arguments[:2] == ("get", "pods"):
            payload = {
                "items": [
                    {"status": {"containerStatuses": [{"state": {"terminated": {"exitCode": 0}}}]}}
                ]
            }
            return subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(payload).encode(), stderr=b""
            )
        if arguments[0] == "logs":
            return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"kubelet unavailable")
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    monkeypatch.setattr(KubernetesJobExecutor, "_kubectl", fake_kubectl)
    monkeypatch.setattr("verirun.executor.time.sleep", lambda _seconds: None)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == "kubernetes_logs_error"


def test_kubernetes_executor_rejects_non_kubernetes_manifest(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(smoke_cases()[0], store)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.POLICY_VIOLATION
    assert result.error_class == "execution_tier_mismatch"


def test_kubernetes_executor_rejects_tampered_artifact_before_job_creation(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    (tmp_path / manifest.candidate.source.relative_path).write_text("tampered", encoding="utf-8")

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == "artifact_integrity_error"


def test_kubernetes_executor_rejects_candidate_syntax_before_job_creation(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    case = SmokeCase(
        name="bad-kubernetes-candidate",
        task_id="Synthetic/BadKubernetesCandidate",
        candidate="def answer(:\n",
        tests="pass\n",
        expected_status=VerificationStatus.COMPILE_ERROR,
    )
    manifest = _kubernetes_manifest(case, store)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.COMPILE_ERROR
    assert result.error_class == "candidate_syntax_error"


def test_kubernetes_executor_rejects_mismatched_image_identity(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    mismatched = manifest.model_copy(
        update={"verifier": manifest.verifier.model_copy(update={"image_digest": "a" * 64})}
    )

    result = KubernetesJobExecutor().execute(mismatched, store, attempt_id="attempt")

    assert result.status is VerificationStatus.POLICY_VIOLATION
    assert result.error_class == "kubernetes_image_identity_mismatch"


def test_kubernetes_executor_reports_job_create_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_kubectl(
        self: KubernetesJobExecutor,
        _context: str,
        _namespace: str,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 45,
    ) -> subprocess.CompletedProcess[bytes]:
        if arguments[:3] == ("get", "networkpolicy", "default-deny-egress"):
            return subprocess.CompletedProcess([], 0, stdout=b"policy", stderr=b"")
        return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"apply unavailable")

    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    monkeypatch.setattr(KubernetesJobExecutor, "_kubectl", fake_kubectl)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == "kubernetes_job_create_error"


def test_kubernetes_executor_waits_for_failed_job_before_collecting_timeout_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_kubectl(
        self: KubernetesJobExecutor,
        _context: str,
        _namespace: str,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 45,
    ) -> subprocess.CompletedProcess[bytes]:
        del self, input_bytes, timeout_seconds
        calls.append(arguments)
        if arguments[:2] == ("get", "networkpolicy"):
            return subprocess.CompletedProcess([], 0, stdout=b"policy", stderr=b"")
        if arguments[0] == "apply":
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        if arguments[0] == "wait" and arguments[1] == "--for=condition=complete":
            return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"deadline")
        if arguments[0] == "wait" and arguments[1] == "--for=condition=failed":
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        if arguments[:2] == ("get", "job"):
            return subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    b'{"status":{"conditions":[{"type":"Failed","status":"True",'
                    b'"reason":"DeadlineExceeded"}]}}'
                ),
                stderr=b"",
            )
        if arguments[:2] == ("get", "pods"):
            return subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    b'{"items":[{"status":{"containerStatuses":[{"state":'
                    b'{"terminated":{"exitCode":137,"reason":"Error"}}}]}}]}'
                ),
                stderr=b"",
            )
        if arguments[0] == "logs":
            return subprocess.CompletedProcess([], 124, stdout=b"", stderr=b"log deadline\n")
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    manifest = manifest.model_copy(
        update={"verifier": manifest.verifier.model_copy(update={"timeout_seconds": 1.0})}
    )
    monkeypatch.setattr(KubernetesJobExecutor, "_kubectl", fake_kubectl)
    monkeypatch.setattr("verirun.executor.time.sleep", lambda _seconds: None)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.TIMEOUT
    assert result.error_class == "wall_time_exceeded"
    assert any(
        call[:2] == ("wait", "--for=condition=failed") and call[-1] == "--timeout=5s"
        for call in calls
    )


def test_kubernetes_job_failure_reason_reads_terminal_deadline() -> None:
    assert (
        KubernetesJobExecutor._job_failure_reason(
            {
                "status": {
                    "conditions": [
                        {"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}
                    ]
                }
            }
        )
        == "DeadlineExceeded"
    )
    assert KubernetesJobExecutor._job_failure_reason({"status": {"conditions": []}}) is None


def test_kubernetes_executor_preserves_exit_zero_despite_late_job_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_kubectl(
        self: KubernetesJobExecutor,
        _context: str,
        _namespace: str,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 45,
    ) -> subprocess.CompletedProcess[bytes]:
        del self, input_bytes, timeout_seconds
        if arguments[:2] == ("get", "networkpolicy"):
            return subprocess.CompletedProcess([], 0, stdout=b"policy", stderr=b"")
        if arguments[0] == "apply":
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        if arguments[0] == "wait":
            return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"not complete")
        if arguments[:2] == ("get", "job"):
            return subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    b'{"status":{"conditions":[{"type":"Failed","status":"True",'
                    b'"reason":"DeadlineExceeded"}]}}'
                ),
                stderr=b"",
            )
        if arguments[:2] == ("get", "pods"):
            return subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    b'{"items":[{"status":{"containerStatuses":[{"state":'
                    b'{"terminated":{"exitCode":0,"reason":"Completed"}}}]}}]}'
                ),
                stderr=b"",
            )
        return subprocess.CompletedProcess([], 0, stdout=b"verified\n", stderr=b"")

    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    monkeypatch.setattr(KubernetesJobExecutor, "_kubectl", fake_kubectl)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.PASSED


@pytest.mark.parametrize(
    ("exit_code", "reason", "wait_code", "expected_status", "expected_error_class"),
    [
        (10, None, 1, VerificationStatus.TEST_FAILURE, "assertion_failure"),
        (11, None, 1, VerificationStatus.TEST_FAILURE, "runtime_error"),
        (137, "OOMKilled", 1, VerificationStatus.OOM, "memory_limit_exceeded"),
        (None, "DeadlineExceeded", 1, VerificationStatus.TIMEOUT, "wall_time_exceeded"),
    ],
)
def test_kubernetes_executor_classifies_terminal_job_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exit_code: int | None,
    reason: str | None,
    wait_code: int,
    expected_status: VerificationStatus,
    expected_error_class: str,
) -> None:
    def fake_kubectl(
        self: KubernetesJobExecutor,
        _context: str,
        _namespace: str,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 45,
    ) -> subprocess.CompletedProcess[bytes]:
        if arguments[:3] == ("get", "networkpolicy", "default-deny-egress"):
            return subprocess.CompletedProcess([], 0, stdout=b"policy", stderr=b"")
        if arguments[0] == "wait":
            return subprocess.CompletedProcess([], wait_code, stdout=b"", stderr=b"job failed")
        if arguments[:2] == ("get", "pods"):
            terminated: dict[str, object] = {}
            if exit_code is not None:
                terminated["exitCode"] = exit_code
            if reason is not None:
                terminated["reason"] = reason
            payload = {
                "items": [
                    {"status": {"containerStatuses": [{"state": {"terminated": terminated}}]}}
                ]
            }
            return subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(payload).encode(), stderr=b""
            )
        if arguments[0] == "logs":
            marker = (
                b"VERIRUN:assertion_failure:wrong\n"
                if exit_code == 10
                else b"VERIRUN:runtime_error:broken\n"
            )
            return subprocess.CompletedProcess([], 0, stdout=marker, stderr=b"")
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    monkeypatch.setattr(KubernetesJobExecutor, "_kubectl", fake_kubectl)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is expected_status
    assert result.error_class == expected_error_class


def test_kubernetes_executor_cleanup_failure_is_not_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_kubectl(
        self: KubernetesJobExecutor,
        _context: str,
        _namespace: str,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 45,
    ) -> subprocess.CompletedProcess[bytes]:
        if arguments[:3] == ("get", "networkpolicy", "default-deny-egress"):
            return subprocess.CompletedProcess([], 0, stdout=b"policy", stderr=b"")
        if arguments[:2] == ("get", "pods"):
            payload = {
                "items": [
                    {"status": {"containerStatuses": [{"state": {"terminated": {"exitCode": 0}}}]}}
                ]
            }
            return subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(payload).encode(), stderr=b""
            )
        if arguments[0] == "logs":
            return subprocess.CompletedProcess([], 0, stdout=b"verified\n", stderr=b"")
        if arguments[0] == "delete":
            return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"delete unavailable")
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    store = ArtifactStore(tmp_path)
    manifest = _kubernetes_manifest(smoke_cases()[0], store)
    monkeypatch.setattr(KubernetesJobExecutor, "_kubectl", fake_kubectl)

    result = KubernetesJobExecutor().execute(manifest, store, attempt_id="attempt")

    assert result.status is VerificationStatus.INFRA_ERROR
    assert result.error_class == "kubernetes_cleanup_error"


def test_kubernetes_kubectl_timeout_is_a_bounded_completed_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def time_out(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired("kubectl", 5)

    monkeypatch.setattr("verirun.executor.subprocess.run", time_out)

    result = KubernetesJobExecutor(kubectl_binary="kubectl-test")._kubectl(
        "context", "namespace", ("logs", "job/example"), timeout_seconds=5
    )

    assert result.returncode == 124
    assert result.stderr == b"VERIRUN:kubectl_timeout\n"
