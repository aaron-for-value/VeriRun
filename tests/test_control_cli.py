from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest

from verirun.cli import main
from verirun.control_plane import (
    AggregationPolicy,
    CreateRunCommand,
    EvaluationIntent,
    PlanCompileRequest,
    RunTaskInput,
    TaskSpec,
    VerifierCatalogEntry,
)
from verirun.models import Sha256

DSN = os.environ.get("VERIRUN_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(DSN is None, reason="VERIRUN_TEST_POSTGRES_DSN is unset")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_control_cli_lifecycle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert DSN is not None
    migrate_output = tmp_path / "migration.json"
    assert main(["control", "--dsn", DSN, "migrate", "--output", str(migrate_output)]) == 0
    assert json.loads(migrate_output.read_text())["migration"] == "ok"

    with psycopg.connect(DSN) as connection:
        connection.execute(
            """TRUNCATE artifact_metadata, final_results, attempt_leases, run_tasks,
                      command_receipts, eval_runs, comparison_cohorts, verification_plans
               CASCADE"""
        )

    request = PlanCompileRequest(
        task_spec=TaskSpec(task_id="task", task_family="python"),
        evaluation_intent=EvaluationIntent(name="correctness"),
        verifier_catalog=(
            VerifierCatalogEntry(
                verifier_id="tests",
                task_families=("python",),
                intents=("correctness",),
                adapter="tests",
                version="1",
                config_digest="a" * 64,
                image_digest="b" * 64,
                required_evidence=("stdout",),
            ),
        ),
        aggregation_policy=AggregationPolicy(
            policy_id="pass-rate",
            version="1",
            reducer="mean",
            config_digest="c" * 64,
        ),
        policy_revision="policy-1",
        comparison_cohort_id="cli-cohort",
        candidate_count=2,
        max_concurrency=2,
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    assert (
        main(
            [
                "control",
                "plan",
                "compile",
                "--request",
                str(request_path),
                "--plan-id",
                "cli-plan",
                "--revision",
                "1",
                "--output",
                str(plan_path),
            ]
        )
        == 0
    )
    plan_payload = json.loads(plan_path.read_text())
    digest: Sha256 = plan_payload["plan_digest"]
    assert main(["control", "--dsn", DSN, "plan", "register", "--plan", str(plan_path)]) == 0
    capsys.readouterr()
    for target in ("validated", "frozen"):
        assert (
            main(
                [
                    "control",
                    "--dsn",
                    DSN,
                    "plan",
                    "transition",
                    "--plan-id",
                    "cli-plan",
                    "--revision",
                    "1",
                    "--target",
                    target,
                    "--reason",
                    f"cli {target}",
                ]
            )
            == 0
        )
        capsys.readouterr()

    command = CreateRunCommand(
        idempotency_key="cli-create",
        run_id="cli-run",
        requested_cohort_id="cli-cohort",
        plan_id="cli-plan",
        plan_revision=1,
        plan_digest=digest,
        tasks=(
            RunTaskInput(task_id="task-a", candidate_id="candidate-a", candidate_hash="d" * 64),
            RunTaskInput(task_id="task-b", candidate_id="candidate-b", candidate_hash="e" * 64),
        ),
    )
    command_path = tmp_path / "create.json"
    command_path.write_text(command.model_dump_json(), encoding="utf-8")
    assert main(["control", "--dsn", DSN, "run", "create", "--command", str(command_path)]) == 0
    capsys.readouterr()
    inspection_path = tmp_path / "inspection.json"
    assert (
        main(
            [
                "control",
                "--dsn",
                DSN,
                "run",
                "inspect",
                "--run-id",
                "cli-run",
                "--output",
                str(inspection_path),
            ]
        )
        == 0
    )
    assert len(json.loads(inspection_path.read_text())["tasks"]) == 2

    lease_path = tmp_path / "lease.json"
    assert (
        main(
            [
                "control",
                "--dsn",
                DSN,
                "task",
                "claim",
                "--run-id",
                "cli-run",
                "--worker-id",
                "worker",
                "--attempt-id",
                "attempt-a",
                "--lease-token",
                "token-a",
                "--lease-seconds",
                "30",
                "--output",
                str(lease_path),
            ]
        )
        == 0
    )
    assert json.loads(lease_path.read_text())["lease"]["attempt_id"] == "attempt-a"
    assert (
        main(
            [
                "control",
                "--dsn",
                DSN,
                "task",
                "heartbeat",
                "--attempt-id",
                "attempt-a",
                "--worker-id",
                "worker",
                "--lease-token",
                "token-a",
                "--lease-seconds",
                "30",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "control",
                "--dsn",
                DSN,
                "run",
                "cancel",
                "--run-id",
                "cli-run",
                "--idempotency-key",
                "cli-cancel",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "control",
                "--dsn",
                DSN,
                "run",
                "resume",
                "--run-id",
                "cli-run",
                "--idempotency-key",
                "cli-resume",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["control", "--dsn", DSN, "task", "reclaim"]) == 0
    capsys.readouterr()

    payload_path = tmp_path / "payload.json"
    write_json(payload_path, {"status": "passed"})
    for suffix, result_digest in (("b", "1" * 64), ("c", "2" * 64)):
        assert (
            main(
                [
                    "control",
                    "--dsn",
                    DSN,
                    "task",
                    "claim",
                    "--run-id",
                    "cli-run",
                    "--worker-id",
                    "worker",
                    "--attempt-id",
                    f"attempt-{suffix}",
                    "--lease-token",
                    f"token-{suffix}",
                    "--lease-seconds",
                    "30",
                ]
            )
            == 0
        )
        capsys.readouterr()
        assert (
            main(
                [
                    "control",
                    "--dsn",
                    DSN,
                    "result",
                    "commit",
                    "--attempt-id",
                    f"attempt-{suffix}",
                    "--worker-id",
                    "worker",
                    "--lease-token",
                    f"token-{suffix}",
                    "--result-digest",
                    result_digest,
                    "--payload",
                    str(payload_path),
                    "--failure-domain",
                    "verifier",
                ]
            )
            == 0
        )
        capsys.readouterr()

    assert (
        main(
            [
                "control",
                "--dsn",
                DSN,
                "run",
                "replay",
                "--source-run-id",
                "cli-run",
                "--replay-run-id",
                "cli-replay",
                "--idempotency-key",
                "cli-replay-command",
            ]
        )
        == 0
    )


def test_control_cli_requires_backend_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERIRUN_POSTGRES_DSN", raising=False)
    with pytest.raises(ValueError, match="VERIRUN_POSTGRES_DSN"):
        main(["control", "migrate"])


def test_control_cli_runs_live_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert DSN is not None
    endpoint = os.environ.get("VERIRUN_TEST_S3_ENDPOINT")
    access_key = os.environ.get("VERIRUN_TEST_S3_ACCESS_KEY")
    secret_key = os.environ.get("VERIRUN_TEST_S3_SECRET_KEY")
    if not endpoint or not access_key or not secret_key:
        pytest.skip("live S3 test variables are unset")
    monkeypatch.setenv("VERIRUN_S3_ACCESS_KEY", access_key)
    monkeypatch.setenv("VERIRUN_S3_SECRET_KEY", secret_key)
    output = tmp_path / "smoke"
    assert (
        main(
            [
                "control",
                "--dsn",
                DSN,
                "smoke",
                "--s3-endpoint",
                endpoint,
                "--s3-bucket",
                "verirun-m3-cli-pytest",
                "--s3-server-identity",
                "minio-test",
                "--no-s3-secure",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert "control_plane_smoke_succeeded=true" in capsys.readouterr().out
    assert json.loads((output / "summary.json").read_text())["succeeded"] is True


def test_control_cli_smoke_requires_s3_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DSN is not None
    monkeypatch.delenv("VERIRUN_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("VERIRUN_S3_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="VERIRUN_S3_ACCESS_KEY"):
        main(
            [
                "control",
                "--dsn",
                DSN,
                "smoke",
                "--s3-endpoint",
                "127.0.0.1:1",
                "--no-s3-secure",
            ]
        )
