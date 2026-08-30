"""Execution backend contracts and the trusted-fixture local backend."""

from __future__ import annotations

import base64
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from verirun.artifacts import ArtifactIntegrityError, ArtifactStore
from verirun.models import (
    AttemptState,
    EvalManifest,
    TaskAttempt,
    VerificationResult,
    VerificationStatus,
)


class Executor(Protocol):
    def execute(
        self,
        manifest: EvalManifest,
        store: ArtifactStore,
        *,
        attempt_id: str | None = None,
    ) -> VerificationResult: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _syntax_message(exc: SyntaxError) -> str:
    location = f"line {exc.lineno}" if exc.lineno is not None else "unknown line"
    return f"{exc.msg} ({location})"


def _truncate(payload: bytes, limit: int) -> tuple[bytes, bool]:
    if len(payload) <= limit:
        return payload, False
    marker = b"\n... output truncated by VeriRun ...\n"
    keep = max(0, limit - len(marker))
    return payload[:keep] + marker, True


def _harness_source(candidate: str, tests: str) -> str:
    candidate_payload = base64.b64encode(candidate.encode("utf-8")).decode("ascii")
    tests_payload = base64.b64encode(tests.encode("utf-8")).decode("ascii")
    return f'''import base64
import sys

candidate = base64.b64decode("{candidate_payload}").decode("utf-8")
tests = base64.b64decode("{tests_payload}").decode("utf-8")
namespace = {{"__name__": "__verirun_candidate__"}}

try:
    exec(compile(candidate, "candidate.py", "exec"), namespace)
    exec(compile(tests, "tests.py", "exec"), namespace)
except AssertionError as exc:
    message = str(exc).replace("\\r", "\\\\r").replace("\\n", "\\\\n")
    sys.stderr.write(f"VERIRUN:assertion_failure:{{message}}\\n")
    raise SystemExit(10)
except BaseException as exc:
    message = str(exc).replace("\\r", "\\\\r").replace("\\n", "\\\\n")
    sys.stderr.write(f"VERIRUN:runtime_error:{{type(exc).__name__}}:{{message}}\\n")
    raise SystemExit(11)
'''


def _container_workspace_directory() -> Path:
    """Return a host path that a development container can bind-mount."""
    return Path.cwd().resolve()


class LocalExecutor:
    """Run trusted fixtures in a subprocess.

    This backend is intentionally not a security boundary. It exists to prove v0.1
    protocol, artifact, timeout, classification, and replay behavior.
    """

    def execute(
        self,
        manifest: EvalManifest,
        store: ArtifactStore,
        *,
        attempt_id: str | None = None,
    ) -> VerificationResult:
        started_at = _utc_now()
        started_clock = time.perf_counter()
        resolved_attempt_id = attempt_id or uuid.uuid4().hex

        try:
            candidate_source = store.read_text(manifest.candidate.source)
            test_source = store.read_text(manifest.verifier.tests)
        except (ArtifactIntegrityError, UnicodeDecodeError) as exc:
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.INFRA_ERROR,
                error_class="artifact_integrity_error",
                error_message=str(exc),
                exit_code=None,
                stdout=b"",
                stderr=f"VERIRUN:artifact_integrity_error:{exc}\n".encode(),
                output_truncated=False,
            )

        try:
            compile(candidate_source, "candidate.py", "exec")
        except SyntaxError as exc:
            message = _syntax_message(exc)
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.COMPILE_ERROR,
                error_class="candidate_syntax_error",
                error_message=message,
                exit_code=None,
                stdout=b"",
                stderr=f"VERIRUN:candidate_syntax_error:{message}\n".encode(),
                output_truncated=False,
            )

        try:
            compile(test_source, "tests.py", "exec")
        except SyntaxError as exc:
            message = _syntax_message(exc)
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.INFRA_ERROR,
                error_class="verifier_syntax_error",
                error_message=message,
                exit_code=None,
                stdout=b"",
                stderr=f"VERIRUN:verifier_syntax_error:{message}\n".encode(),
                output_truncated=False,
            )

        with tempfile.TemporaryDirectory(prefix="verirun-local-") as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "runner.py").write_text(
                _harness_source(candidate_source, test_source), encoding="utf-8"
            )
            environment = {
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": os.environ.get("PATH", ""),
                "PYTHONHASHSEED": "0",
            }
            try:
                process = subprocess.Popen(
                    [sys.executable, "-I", "runner.py"],
                    cwd=directory,
                    env=environment,
                    start_new_session=True,
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
            except OSError as exc:
                return self._result(
                    manifest=manifest,
                    store=store,
                    attempt_id=resolved_attempt_id,
                    started_at=started_at,
                    started_clock=started_clock,
                    status=VerificationStatus.INFRA_ERROR,
                    error_class="process_start_error",
                    error_message=str(exc),
                    exit_code=None,
                    stdout=b"",
                    stderr=f"VERIRUN:process_start_error:{exc}\n".encode(),
                    output_truncated=False,
                )
            timed_out = False
            try:
                stdout, stderr = process.communicate(timeout=manifest.verifier.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
                stderr += b"VERIRUN:timeout:wall_time_exceeded\n"

        stdout, stdout_truncated = _truncate(stdout, manifest.verifier.max_output_bytes)
        stderr, stderr_truncated = _truncate(stderr, manifest.verifier.max_output_bytes)
        truncated = stdout_truncated or stderr_truncated

        if timed_out:
            status = VerificationStatus.TIMEOUT
            error_class = "wall_time_exceeded"
            error_message = f"exceeded {manifest.verifier.timeout_seconds:g} seconds"
        elif process.returncode == 0:
            status = VerificationStatus.PASSED
            error_class = None
            error_message = None
        elif process.returncode == 10:
            status = VerificationStatus.TEST_FAILURE
            error_class = "assertion_failure"
            error_message = self._marker_message(stderr, "assertion_failure")
        elif process.returncode == 11:
            status = VerificationStatus.TEST_FAILURE
            error_class = "runtime_error"
            error_message = self._marker_message(stderr, "runtime_error")
        else:
            status = VerificationStatus.INFRA_ERROR
            error_class = "process_signal" if process.returncode < 0 else "unknown_exit_code"
            error_message = f"runner exited with code {process.returncode}"

        return self._result(
            manifest=manifest,
            store=store,
            attempt_id=resolved_attempt_id,
            started_at=started_at,
            started_clock=started_clock,
            status=status,
            error_class=error_class,
            error_message=error_message,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            output_truncated=truncated,
        )

    @staticmethod
    def _marker_message(stderr: bytes, marker: str) -> str:
        prefix = f"VERIRUN:{marker}:"
        for line in stderr.decode("utf-8", errors="replace").splitlines():
            if line.startswith(prefix):
                return line.removeprefix(prefix)
        return marker

    @staticmethod
    def _result(
        *,
        manifest: EvalManifest,
        store: ArtifactStore,
        attempt_id: str,
        started_at: datetime,
        started_clock: float,
        status: VerificationStatus,
        error_class: str | None,
        error_message: str | None,
        exit_code: int | None,
        stdout: bytes,
        stderr: bytes,
        output_truncated: bool,
    ) -> VerificationResult:
        finished_at = _utc_now()
        duration_ms = max(0, round((time.perf_counter() - started_clock) * 1000))
        stdout_ref = store.put_bytes(
            kind="stdout", payload=stdout, media_type="text/plain; charset=utf-8"
        )
        stderr_ref = store.put_bytes(
            kind="stderr", payload=stderr, media_type="text/plain; charset=utf-8"
        )
        attempt = TaskAttempt(
            attempt_id=attempt_id,
            run_id=manifest.run_id,
            task_id=manifest.candidate.task_id,
            candidate_id=manifest.candidate.candidate_id,
            state=AttemptState.FINISHED,
            started_at=started_at,
            finished_at=finished_at,
        )
        return VerificationResult(
            attempt=attempt,
            status=status,
            error_class=error_class,
            error_message=error_message,
            candidate_hash=manifest.candidate.source.sha256,
            test_hash=manifest.verifier.tests.sha256,
            verifier_version=manifest.verifier.version,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout=stdout_ref,
            stderr=stderr_ref,
            output_truncated=output_truncated,
        )


class ContainerExecutor(LocalExecutor):
    """Run candidates in the explicitly limited development-container tier.

    This is operational risk reduction, not a claim of strong isolation. In
    particular, it does not substitute for the Linux/Kubernetes gVisor evidence
    required by the v0.3 roadmap.
    """

    def __init__(self, *, runtime_binary: str = "docker") -> None:
        self.runtime_binary = runtime_binary

    def execute(
        self,
        manifest: EvalManifest,
        store: ArtifactStore,
        *,
        attempt_id: str | None = None,
    ) -> VerificationResult:
        started_at = _utc_now()
        started_clock = time.perf_counter()
        resolved_attempt_id = attempt_id or uuid.uuid4().hex

        if manifest.execution.engine != "container":
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.POLICY_VIOLATION,
                error_class="execution_tier_mismatch",
                error_message="container executor requires engine=container",
                exit_code=None,
                stdout=b"",
                stderr=(
                    b"VERIRUN:execution_tier_mismatch:"
                    b"container executor requires engine=container\n"
                ),
                output_truncated=False,
            )

        try:
            candidate_source = store.read_text(manifest.candidate.source)
            test_source = store.read_text(manifest.verifier.tests)
        except (ArtifactIntegrityError, UnicodeDecodeError) as exc:
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.INFRA_ERROR,
                error_class="artifact_integrity_error",
                error_message=str(exc),
                exit_code=None,
                stdout=b"",
                stderr=f"VERIRUN:artifact_integrity_error:{exc}\n".encode(),
                output_truncated=False,
            )

        try:
            compile(candidate_source, "candidate.py", "exec")
        except SyntaxError as exc:
            message = _syntax_message(exc)
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.COMPILE_ERROR,
                error_class="candidate_syntax_error",
                error_message=message,
                exit_code=None,
                stdout=b"",
                stderr=f"VERIRUN:candidate_syntax_error:{message}\n".encode(),
                output_truncated=False,
            )

        try:
            compile(test_source, "tests.py", "exec")
        except SyntaxError as exc:
            message = _syntax_message(exc)
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.INFRA_ERROR,
                error_class="verifier_syntax_error",
                error_message=message,
                exit_code=None,
                stdout=b"",
                stderr=f"VERIRUN:verifier_syntax_error:{message}\n".encode(),
                output_truncated=False,
            )

        image = manifest.execution.container_image
        cpus = manifest.execution.container_cpus
        memory_mb = manifest.execution.container_memory_mb
        pids_limit = manifest.execution.container_pids_limit
        assert (
            image is not None
            and cpus is not None
            and memory_mb is not None
            and pids_limit is not None
        )
        if manifest.verifier.image_digest != image.rsplit("@", maxsplit=1)[1]:
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.POLICY_VIOLATION,
                error_class="container_image_identity_mismatch",
                error_message="verifier image digest does not match execution image",
                exit_code=None,
                stdout=b"",
                stderr=b"VERIRUN:container_image_identity_mismatch\n",
                output_truncated=False,
            )
        container_name = f"verirun-{uuid.uuid4().hex}"
        cleanup_error: str | None = None
        oom_killed = False

        # Docker Desktop alternatives such as Colima only bind-mount host paths
        # that are shared with their VM.  The host OS temporary directory is not
        # a portable shared path, whereas the caller's workspace is the declared
        # execution boundary for this development tier.
        with tempfile.TemporaryDirectory(
            prefix=".verirun-container-", dir=_container_workspace_directory()
        ) as temporary_directory:
            directory = Path(temporary_directory)
            runner = directory / "runner.py"
            runner.write_text(_harness_source(candidate_source, test_source), encoding="utf-8")
            directory.chmod(0o755)
            runner.chmod(0o444)
            command = self._container_command(
                directory=directory,
                container_name=container_name,
                image=image,
                cpus=cpus,
                memory_mb=memory_mb,
                pids_limit=pids_limit,
            )
            environment = {
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": os.environ.get("PATH", ""),
            }
            try:
                process = subprocess.Popen(
                    command,
                    cwd=directory,
                    env=environment,
                    start_new_session=True,
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
            except OSError as exc:
                return self._result(
                    manifest=manifest,
                    store=store,
                    attempt_id=resolved_attempt_id,
                    started_at=started_at,
                    started_clock=started_clock,
                    status=VerificationStatus.INFRA_ERROR,
                    error_class="container_start_error",
                    error_message=str(exc),
                    exit_code=None,
                    stdout=b"",
                    stderr=f"VERIRUN:container_start_error:{exc}\n".encode(),
                    output_truncated=False,
                )
            timed_out = False
            try:
                stdout, stderr = process.communicate(timeout=manifest.verifier.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_result = self._maintenance(("kill", container_name))
                if kill_result.returncode != 0 and not self._missing_container(kill_result):
                    cleanup_error = self._maintenance_error("kill", kill_result)
                # Docker's container-level kill is authoritative. Some host
                # sandboxes forbid signalling the docker CLI process group even
                # after that kill has succeeded, so do not turn the timeout path
                # into an uncaught host-permission error.
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
                stderr += b"VERIRUN:timeout:wall_time_exceeded\n"
            if not timed_out and process.returncode == 137:
                inspect_result = self._maintenance(
                    ("inspect", "--format", "{{.State.OOMKilled}}", container_name)
                )
                oom_killed = (
                    inspect_result.returncode == 0 and inspect_result.stdout.strip() == b"true"
                )
            remove_result = self._maintenance(("rm", "--force", container_name))
            if remove_result.returncode != 0 and not self._missing_container(remove_result):
                cleanup_error = cleanup_error or self._maintenance_error("remove", remove_result)

        stdout, stdout_truncated = _truncate(stdout, manifest.verifier.max_output_bytes)
        stderr, stderr_truncated = _truncate(stderr, manifest.verifier.max_output_bytes)
        truncated = stdout_truncated or stderr_truncated

        if cleanup_error is not None:
            status = VerificationStatus.INFRA_ERROR
            error_class = "container_cleanup_error"
            error_message = cleanup_error
        elif timed_out:
            status = VerificationStatus.TIMEOUT
            error_class = "wall_time_exceeded"
            error_message = f"exceeded {manifest.verifier.timeout_seconds:g} seconds"
        elif process.returncode == 0:
            status = VerificationStatus.PASSED
            error_class = None
            error_message = None
        elif process.returncode == 10:
            status = VerificationStatus.TEST_FAILURE
            error_class = "assertion_failure"
            error_message = self._marker_message(stderr, "assertion_failure")
        elif process.returncode == 11:
            status = VerificationStatus.TEST_FAILURE
            error_class = "runtime_error"
            error_message = self._marker_message(stderr, "runtime_error")
        elif oom_killed:
            status = VerificationStatus.OOM
            error_class = "memory_limit_exceeded"
            error_message = "container runtime reported OOMKilled"
        else:
            status = VerificationStatus.INFRA_ERROR
            error_class = (
                "container_signal" if process.returncode < 0 else "container_runtime_error"
            )
            error_message = f"container runtime exited with code {process.returncode}"

        return self._result(
            manifest=manifest,
            store=store,
            attempt_id=resolved_attempt_id,
            started_at=started_at,
            started_clock=started_clock,
            status=status,
            error_class=error_class,
            error_message=error_message,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            output_truncated=truncated,
        )

    def _container_command(
        self,
        *,
        directory: Path,
        container_name: str,
        image: str,
        cpus: float,
        memory_mb: int,
        pids_limit: int,
    ) -> list[str]:
        return [
            self.runtime_binary,
            "run",
            "--pull=never",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--workdir",
            "/work",
            "--mount",
            f"type=bind,src={directory},dst=/work,readonly",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(pids_limit),
            "--memory",
            f"{memory_mb}m",
            "--cpus",
            str(cpus),
            image,
            "python",
            "-I",
            "/work/runner.py",
        ]

    def _maintenance(self, arguments: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [self.runtime_binary, *arguments],
            check=False,
            capture_output=True,
            timeout=5,
        )

    @staticmethod
    def _missing_container(result: subprocess.CompletedProcess[bytes]) -> bool:
        return b"No such container" in result.stderr

    @staticmethod
    def _maintenance_error(operation: str, result: subprocess.CompletedProcess[bytes]) -> str:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        return f"docker {operation} failed with code {result.returncode}: {detail}"


class KubernetesJobExecutor(LocalExecutor):
    """Run an attempt as one restricted Kubernetes Job using a gVisor RuntimeClass.

    The target namespace is an operator-provisioned boundary: this executor checks
    for its required default-deny egress policy before creating a Job. Candidate
    and verifier inputs are verified artifacts that become the immutable runner
    payload; no host path or writable artifact volume is exposed to the Pod.
    """

    def __init__(self, *, kubectl_binary: str = "kubectl") -> None:
        self.kubectl_binary = kubectl_binary

    def execute(
        self,
        manifest: EvalManifest,
        store: ArtifactStore,
        *,
        attempt_id: str | None = None,
    ) -> VerificationResult:
        started_at = _utc_now()
        started_clock = time.perf_counter()
        resolved_attempt_id = attempt_id or uuid.uuid4().hex

        if manifest.execution.engine != "kubernetes":
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.POLICY_VIOLATION,
                error_class="execution_tier_mismatch",
                error_message="Kubernetes executor requires engine=kubernetes",
                exit_code=None,
                stdout=b"",
                stderr=(
                    b"VERIRUN:execution_tier_mismatch:"
                    b"kubernetes executor requires engine=kubernetes\n"
                ),
                output_truncated=False,
            )

        try:
            candidate_source = store.read_text(manifest.candidate.source)
            test_source = store.read_text(manifest.verifier.tests)
        except (ArtifactIntegrityError, UnicodeDecodeError) as exc:
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.INFRA_ERROR,
                error_class="artifact_integrity_error",
                error_message=str(exc),
                exit_code=None,
                stdout=b"",
                stderr=f"VERIRUN:artifact_integrity_error:{exc}\n".encode(),
                output_truncated=False,
            )

        for source, filename, source_status, source_error_class in (
            (
                candidate_source,
                "candidate.py",
                VerificationStatus.COMPILE_ERROR,
                "candidate_syntax_error",
            ),
            (test_source, "tests.py", VerificationStatus.INFRA_ERROR, "verifier_syntax_error"),
        ):
            try:
                compile(source, filename, "exec")
            except SyntaxError as exc:
                message = _syntax_message(exc)
                return self._result(
                    manifest=manifest,
                    store=store,
                    attempt_id=resolved_attempt_id,
                    started_at=started_at,
                    started_clock=started_clock,
                    status=source_status,
                    error_class=source_error_class,
                    error_message=message,
                    exit_code=None,
                    stdout=b"",
                    stderr=f"VERIRUN:{source_error_class}:{message}\n".encode(),
                    output_truncated=False,
                )

        execution = manifest.execution
        image = execution.container_image
        cpus = execution.container_cpus
        memory_mb = execution.container_memory_mb
        context = execution.kubernetes_context
        namespace = execution.kubernetes_namespace
        runtime_class = execution.kubernetes_runtime_class
        assert all(
            value is not None
            for value in (image, cpus, memory_mb, context, namespace, runtime_class)
        )
        assert image is not None and cpus is not None and memory_mb is not None
        assert context is not None and namespace is not None and runtime_class is not None

        if manifest.verifier.image_digest != image.rsplit("@", maxsplit=1)[1]:
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.POLICY_VIOLATION,
                error_class="kubernetes_image_identity_mismatch",
                error_message="verifier image digest does not match execution image",
                exit_code=None,
                stdout=b"",
                stderr=b"VERIRUN:kubernetes_image_identity_mismatch\n",
                output_truncated=False,
            )

        policy = self._kubectl(context, namespace, ("get", "networkpolicy", "default-deny-egress"))
        if policy.returncode != 0:
            detail = policy.stderr.decode("utf-8", errors="replace").strip()
            return self._result(
                manifest=manifest,
                store=store,
                attempt_id=resolved_attempt_id,
                started_at=started_at,
                started_clock=started_clock,
                status=VerificationStatus.POLICY_VIOLATION,
                error_class="default_deny_egress_missing",
                error_message=detail or "required default-deny-egress NetworkPolicy is unavailable",
                exit_code=None,
                stdout=b"",
                stderr=policy.stderr,
                output_truncated=False,
            )

        job_name = f"verirun-{uuid.uuid4().hex[:20]}"
        job = self._job_document(
            job_name=job_name,
            namespace=namespace,
            runtime_class=runtime_class,
            image=image,
            cpus=cpus,
            memory_mb=memory_mb,
            timeout_seconds=manifest.verifier.timeout_seconds,
            runner=_harness_source(candidate_source, test_source),
        )
        created = False
        cleanup_error: str | None = None
        wait_result: subprocess.CompletedProcess[bytes] | None = None
        job_payload: dict[str, object] = {}
        pod_payload: dict[str, object] = {}
        logs = b""
        logs_error: str | None = None
        try:
            created_result = self._kubectl(
                context, namespace, ("apply", "-f", "-"), input_bytes=json.dumps(job).encode()
            )
            if created_result.returncode != 0:
                detail = created_result.stderr.decode("utf-8", errors="replace").strip()
                return self._result(
                    manifest=manifest,
                    store=store,
                    attempt_id=resolved_attempt_id,
                    started_at=started_at,
                    started_clock=started_clock,
                    status=VerificationStatus.INFRA_ERROR,
                    error_class="kubernetes_job_create_error",
                    error_message=detail or "kubectl apply failed",
                    exit_code=None,
                    stdout=b"",
                    stderr=created_result.stderr,
                    output_truncated=False,
                )
            created = True
            wait_result = self._kubectl(
                context,
                namespace,
                (
                    "wait",
                    "--for=condition=complete",
                    f"job/{job_name}",
                    f"--timeout={max(1, math.ceil(manifest.verifier.timeout_seconds) + 10)}s",
                ),
            )
            if wait_result.returncode != 0:
                # A deadline or verifier failure cannot satisfy ``Complete``. Wait for
                # the Job controller to publish its terminal failure before querying
                # the Pod and logs; otherwise a still-terminating Pod can make a
                # correctly classified timeout look like a log transport outage.
                self._kubectl(
                    context,
                    namespace,
                    (
                        "wait",
                        "--for=condition=failed",
                        f"job/{job_name}",
                        "--timeout=5s",
                    ),
                )
            job_result = self._kubectl(context, namespace, ("get", "job", job_name, "-o", "json"))
            if job_result.returncode == 0:
                try:
                    parsed = json.loads(job_result.stdout)
                    if isinstance(parsed, dict):
                        job_payload = parsed
                except json.JSONDecodeError:
                    pass
            pods_result = self._kubectl(
                context,
                namespace,
                ("get", "pods", "-l", f"job-name={job_name}", "-o", "json"),
            )
            if pods_result.returncode == 0:
                try:
                    parsed = json.loads(pods_result.stdout)
                    if isinstance(parsed, dict):
                        pod_payload = parsed
                except json.JSONDecodeError:
                    pass
            logs_result = self._collect_logs(context, namespace, job_name)
            logs = logs_result.stdout + logs_result.stderr
            if logs_result.returncode != 0:
                logs_error = f"kubectl logs failed with code {logs_result.returncode}"
                logs = b"VERIRUN:kubernetes_logs_error\n"
        finally:
            if created:
                deleted = self._kubectl(
                    context,
                    namespace,
                    (
                        "delete",
                        "job",
                        job_name,
                        "--ignore-not-found=true",
                        "--wait=true",
                        "--timeout=30s",
                    ),
                )
                if deleted.returncode != 0:
                    detail = deleted.stderr.decode("utf-8", errors="replace").strip()
                    cleanup_error = detail or "kubectl delete job failed"

        logs, truncated = _truncate(logs, manifest.verifier.max_output_bytes)
        exit_code, termination_reason = self._pod_termination(pod_payload)
        job_failure_reason = self._job_failure_reason(job_payload)
        deadline_exceeded = (
            wait_result is not None
            and wait_result.returncode != 0
            and exit_code != 0
            and (
                termination_reason == "DeadlineExceeded" or job_failure_reason == "DeadlineExceeded"
            )
        )
        status: VerificationStatus
        error_class: str | None
        error_message: str | None
        if cleanup_error is not None:
            status = VerificationStatus.INFRA_ERROR
            error_class = "kubernetes_cleanup_error"
            error_message = cleanup_error
        elif logs_error is not None and not deadline_exceeded:
            status = VerificationStatus.INFRA_ERROR
            error_class = "kubernetes_logs_error"
            error_message = logs_error
        elif wait_result is None:
            status = VerificationStatus.INFRA_ERROR
            error_class = "kubernetes_wait_error"
            error_message = "kubectl wait did not run"
        elif deadline_exceeded:
            status = VerificationStatus.TIMEOUT
            error_class = "wall_time_exceeded"
            error_message = f"exceeded {manifest.verifier.timeout_seconds:g} seconds"
        elif termination_reason == "OOMKilled":
            status = VerificationStatus.OOM
            error_class = "memory_limit_exceeded"
            error_message = "Kubernetes reported OOMKilled"
        elif exit_code == 0:
            status = VerificationStatus.PASSED
            error_class = None
            error_message = None
        elif exit_code == 10:
            status = VerificationStatus.TEST_FAILURE
            error_class = "assertion_failure"
            error_message = self._marker_message(logs, "assertion_failure")
        elif exit_code == 11:
            status = VerificationStatus.TEST_FAILURE
            error_class = "runtime_error"
            error_message = self._marker_message(logs, "runtime_error")
        elif wait_result.returncode != 0:
            status = VerificationStatus.INFRA_ERROR
            error_class = "kubernetes_job_failed"
            error_message = (
                wait_result.stderr.decode("utf-8", errors="replace").strip()
                or "Job did not complete"
            )
        else:
            status = VerificationStatus.INFRA_ERROR
            error_class = "kubernetes_runtime_error"
            error_message = f"Job runner exited with code {exit_code}"

        return self._result(
            manifest=manifest,
            store=store,
            attempt_id=resolved_attempt_id,
            started_at=started_at,
            started_clock=started_clock,
            status=status,
            error_class=error_class,
            error_message=error_message,
            exit_code=exit_code,
            stdout=logs,
            stderr=b"",
            output_truncated=truncated,
        )

    def _kubectl(
        self,
        context: str,
        namespace: str,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 45,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [self.kubectl_binary, "--context", context, "--namespace", namespace, *arguments]
        try:
            return subprocess.run(
                command,
                check=False,
                capture_output=True,
                input=input_bytes,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                command, 124, stdout=b"", stderr=b"VERIRUN:kubectl_timeout\n"
            )

    def _collect_logs(
        self, context: str, namespace: str, job_name: str
    ) -> subprocess.CompletedProcess[bytes]:
        result: subprocess.CompletedProcess[bytes] | None = None
        for _ in range(3):
            result = self._kubectl(
                context,
                namespace,
                ("logs", f"job/{job_name}", "--all-containers=true", "--tail=-1"),
                timeout_seconds=5,
            )
            if result.returncode == 0:
                return result
            time.sleep(1)
        assert result is not None
        return result

    @staticmethod
    def _job_document(
        *,
        job_name: str,
        namespace: str,
        runtime_class: str,
        image: str,
        cpus: float,
        memory_mb: int,
        timeout_seconds: float,
        runner: str,
    ) -> dict[str, object]:
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "namespace": namespace,
                "labels": {"app.kubernetes.io/name": "verirun"},
            },
            "spec": {
                "backoffLimit": 0,
                "completions": 1,
                "parallelism": 1,
                "activeDeadlineSeconds": max(1, math.ceil(timeout_seconds)),
                "ttlSecondsAfterFinished": 60,
                "template": {
                    "metadata": {"labels": {"app.kubernetes.io/name": "verirun"}},
                    "spec": {
                        "runtimeClassName": runtime_class,
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
                        "containers": [
                            {
                                "name": "verifier",
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python", "-I", "-c", runner],
                                "resources": {
                                    "requests": {"cpu": f"{cpus:g}", "memory": f"{memory_mb}Mi"},
                                    "limits": {"cpu": f"{cpus:g}", "memory": f"{memory_mb}Mi"},
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "privileged": False,
                                    "readOnlyRootFilesystem": True,
                                    "runAsNonRoot": True,
                                    "runAsUser": 65534,
                                    "runAsGroup": 65534,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                            }
                        ],
                    },
                },
            },
        }

    @staticmethod
    def _job_failure_reason(payload: dict[str, object]) -> str | None:
        status = payload.get("status")
        if not isinstance(status, dict):
            return None
        conditions = status.get("conditions")
        if not isinstance(conditions, list):
            return None
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            if condition.get("type") not in {"Failed", "FailureTarget"}:
                continue
            if condition.get("status") != "True":
                continue
            reason = condition.get("reason")
            if isinstance(reason, str):
                return reason
        return None

    @staticmethod
    def _pod_termination(payload: dict[str, object]) -> tuple[int | None, str | None]:
        items = payload.get("items")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            return None, None
        status = items[0].get("status")
        if not isinstance(status, dict):
            return None, None
        reason = status.get("reason")
        containers = status.get("containerStatuses")
        if (
            not isinstance(containers, list)
            or not containers
            or not isinstance(containers[0], dict)
        ):
            return None, reason if isinstance(reason, str) else None
        state = containers[0].get("state")
        if not isinstance(state, dict):
            return None, reason if isinstance(reason, str) else None
        terminated = state.get("terminated")
        if not isinstance(terminated, dict):
            return None, reason if isinstance(reason, str) else None
        exit_code = terminated.get("exitCode")
        terminated_reason = terminated.get("reason")
        return (
            exit_code if isinstance(exit_code, int) else None,
            terminated_reason
            if isinstance(terminated_reason, str)
            else (reason if isinstance(reason, str) else None),
        )


def executor_for(manifest: EvalManifest) -> Executor:
    """Select the only executor compatible with a frozen execution tier."""

    if manifest.execution.engine == "local":
        return LocalExecutor()
    if manifest.execution.engine == "container":
        return ContainerExecutor()
    return KubernetesJobExecutor()
