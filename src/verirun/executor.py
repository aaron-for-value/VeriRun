"""Execution backend contracts and the trusted-fixture local backend."""

from __future__ import annotations

import base64
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
