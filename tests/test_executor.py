from __future__ import annotations

from pathlib import Path

import pytest

from verirun.artifacts import ArtifactStore
from verirun.executor import LocalExecutor
from verirun.fixtures import SmokeCase, build_smoke_manifest, smoke_cases
from verirun.models import VerificationStatus


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
