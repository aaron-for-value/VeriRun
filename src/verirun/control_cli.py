"""Typed CLI surface for the PostgreSQL M3 control plane."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from verirun.canonical import canonical_json_bytes, write_canonical_json
from verirun.control_plane import (
    CreateRunCommand,
    FailureDomain,
    PlanCompileRequest,
    PlanState,
    RunInspection,
    VerificationPlan,
    compile_verification_plan,
)

if TYPE_CHECKING:
    from verirun.postgres import PostgresControlPlane


def _backend(args: argparse.Namespace) -> PostgresControlPlane:
    try:
        from verirun.postgres import PostgresControlPlane
    except ImportError as exc:
        raise RuntimeError("install VeriRun with the control-plane extra") from exc
    dsn = args.dsn or os.environ.get("VERIRUN_POSTGRES_DSN")
    if not dsn:
        raise ValueError("set --dsn or VERIRUN_POSTGRES_DSN")
    return PostgresControlPlane(dsn)


def _emit(value: BaseModel | dict[str, object], output: Path | None) -> None:
    if output is None:
        print(canonical_json_bytes(value).decode("utf-8"))
    else:
        write_canonical_json(output, value)


def _load(path: Path, model: type[BaseModel]) -> BaseModel:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _migrate(args: argparse.Namespace) -> int:
    _backend(args).migrate()
    _emit({"migration": "ok"}, args.output)
    return 0


def _plan_compile(args: argparse.Namespace) -> int:
    request = PlanCompileRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
    plan = compile_verification_plan(
        plan_id=args.plan_id,
        revision=args.revision,
        request=request,
    )
    _emit(plan, args.output)
    return 0


def _plan_register(args: argparse.Namespace) -> int:
    plan = VerificationPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
    _emit(_backend(args).register_plan(plan), args.output)
    return 0


def _plan_transition(args: argparse.Namespace) -> int:
    plan = _backend(args).transition_plan(
        args.plan_id,
        args.revision,
        PlanState(args.target),
        reason=args.reason,
    )
    _emit(plan, args.output)
    return 0


def _run_create(args: argparse.Namespace) -> int:
    command = CreateRunCommand.model_validate_json(args.command.read_text(encoding="utf-8"))
    run = _backend(args).create_run(
        idempotency_key=command.idempotency_key,
        run_id=command.run_id,
        requested_cohort_id=command.requested_cohort_id,
        plan_id=command.plan_id,
        plan_revision=command.plan_revision,
        plan_digest=command.plan_digest,
        tasks=command.tasks,
        source_run_id=command.source_run_id,
    )
    _emit(run, args.output)
    return 0


def _run_inspect(args: argparse.Namespace) -> int:
    run, tasks = _backend(args).inspect_run(args.run_id)
    _emit(RunInspection(run=run, tasks=tasks), args.output)
    return 0


def _run_cancel(args: argparse.Namespace) -> int:
    run = _backend(args).cancel_run(args.run_id, idempotency_key=args.idempotency_key)
    _emit(run, args.output)
    return 0


def _run_resume(args: argparse.Namespace) -> int:
    run = _backend(args).resume_run(args.run_id, idempotency_key=args.idempotency_key)
    _emit(run, args.output)
    return 0


def _run_replay(args: argparse.Namespace) -> int:
    run = _backend(args).replay_run(
        args.source_run_id,
        replay_run_id=args.replay_run_id,
        idempotency_key=args.idempotency_key,
    )
    _emit(run, args.output)
    return 0


def _task_claim(args: argparse.Namespace) -> int:
    lease = _backend(args).claim_task(
        run_id=args.run_id,
        worker_id=args.worker_id,
        attempt_id=args.attempt_id,
        lease_token=args.lease_token,
        lease_seconds=args.lease_seconds,
    )
    _emit({"lease": None if lease is None else lease.model_dump(mode="json")}, args.output)
    return 0


def _task_heartbeat(args: argparse.Namespace) -> int:
    lease = _backend(args).heartbeat(
        args.attempt_id,
        worker_id=args.worker_id,
        lease_token=args.lease_token,
        lease_seconds=args.lease_seconds,
    )
    _emit(lease, args.output)
    return 0


def _task_reclaim(args: argparse.Namespace) -> int:
    reclaimed = _backend(args).reclaim_expired()
    _emit({"reclaimed": reclaimed}, args.output)
    return 0


def _result_commit(args: argparse.Namespace) -> int:
    payload: Any = args.payload.read_text(encoding="utf-8")
    import json

    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("result payload must be a JSON object")
    outcome = _backend(args).commit_result(
        args.attempt_id,
        worker_id=args.worker_id,
        lease_token=args.lease_token,
        result_digest=args.result_digest,
        result_payload=decoded,
        failure_domain=None if args.failure_domain is None else FailureDomain(args.failure_domain),
    )
    _emit(outcome, args.output)
    return 0


def _smoke(args: argparse.Namespace) -> int:
    from verirun.control_plane_smoke import run_control_plane_smoke

    access_key = os.environ.get("VERIRUN_S3_ACCESS_KEY")
    secret_key = os.environ.get("VERIRUN_S3_SECRET_KEY")
    if not access_key or not secret_key:
        raise ValueError("set VERIRUN_S3_ACCESS_KEY and VERIRUN_S3_SECRET_KEY")
    summary = run_control_plane_smoke(
        args.output,
        dsn=_backend(args).dsn,
        s3_endpoint=args.s3_endpoint,
        s3_access_key=access_key,
        s3_secret_key=secret_key,
        s3_bucket=args.s3_bucket,
        s3_secure=args.s3_secure,
        s3_server_identity=args.s3_server_identity,
    )
    print(f"control_plane_smoke_succeeded={str(summary['succeeded']).lower()}")
    return 0 if summary["succeeded"] else 10


def _common_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, help="write canonical JSON to this path")


def add_control_parser(subparsers: Any) -> None:
    control = subparsers.add_parser("control", help="operate the v0.4 durable control plane")
    control.add_argument("--dsn", help="PostgreSQL DSN; defaults to VERIRUN_POSTGRES_DSN")
    groups = control.add_subparsers(dest="control_group", required=True)

    migrate = groups.add_parser("migrate", help="apply idempotent PostgreSQL migrations")
    _common_output(migrate)
    migrate.set_defaults(handler=_migrate)

    smoke = groups.add_parser("smoke", help="run live PostgreSQL and S3 recovery evidence")
    smoke.add_argument("--s3-endpoint", required=True)
    smoke.add_argument("--s3-bucket", default="verirun-m3-smoke")
    smoke.add_argument("--s3-server-identity", default="unreported")
    smoke.add_argument("--s3-secure", action=argparse.BooleanOptionalAction, default=True)
    smoke.add_argument("--output", type=Path, default=Path(".verirun/evidence/v0.4/control-plane"))
    smoke.set_defaults(handler=_smoke)

    plan = groups.add_parser("plan", help="compile and manage verification plans")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    compile_command = plan_commands.add_parser("compile")
    compile_command.add_argument("--request", type=Path, required=True)
    compile_command.add_argument("--plan-id", required=True)
    compile_command.add_argument("--revision", type=int, required=True)
    _common_output(compile_command)
    compile_command.set_defaults(handler=_plan_compile)
    register = plan_commands.add_parser("register")
    register.add_argument("--plan", type=Path, required=True)
    _common_output(register)
    register.set_defaults(handler=_plan_register)
    transition = plan_commands.add_parser("transition")
    transition.add_argument("--plan-id", required=True)
    transition.add_argument("--revision", type=int, required=True)
    transition.add_argument(
        "--target", choices=tuple(state.value for state in PlanState), required=True
    )
    transition.add_argument("--reason", required=True)
    _common_output(transition)
    transition.set_defaults(handler=_plan_transition)

    run = groups.add_parser("run", help="create, inspect, cancel, resume, or replay runs")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    create = run_commands.add_parser("create")
    create.add_argument("--command", type=Path, required=True)
    _common_output(create)
    create.set_defaults(handler=_run_create)
    inspect = run_commands.add_parser("inspect")
    inspect.add_argument("--run-id", required=True)
    _common_output(inspect)
    inspect.set_defaults(handler=_run_inspect)
    for name, handler in (("cancel", _run_cancel), ("resume", _run_resume)):
        command = run_commands.add_parser(name)
        command.add_argument("--run-id", required=True)
        command.add_argument("--idempotency-key", required=True)
        _common_output(command)
        command.set_defaults(handler=handler)
    replay = run_commands.add_parser("replay")
    replay.add_argument("--source-run-id", required=True)
    replay.add_argument("--replay-run-id", required=True)
    replay.add_argument("--idempotency-key", required=True)
    _common_output(replay)
    replay.set_defaults(handler=_run_replay)

    task = groups.add_parser("task", help="claim, heartbeat, and reclaim task leases")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    claim = task_commands.add_parser("claim")
    claim.add_argument("--run-id", required=True)
    claim.add_argument("--worker-id", required=True)
    claim.add_argument("--attempt-id", required=True)
    claim.add_argument("--lease-token", required=True)
    claim.add_argument("--lease-seconds", type=float, required=True)
    _common_output(claim)
    claim.set_defaults(handler=_task_claim)
    heartbeat = task_commands.add_parser("heartbeat")
    heartbeat.add_argument("--attempt-id", required=True)
    heartbeat.add_argument("--worker-id", required=True)
    heartbeat.add_argument("--lease-token", required=True)
    heartbeat.add_argument("--lease-seconds", type=float, required=True)
    _common_output(heartbeat)
    heartbeat.set_defaults(handler=_task_heartbeat)
    reclaim = task_commands.add_parser("reclaim")
    _common_output(reclaim)
    reclaim.set_defaults(handler=_task_reclaim)

    result = groups.add_parser("result", help="commit an authoritative result")
    result_commands = result.add_subparsers(dest="result_command", required=True)
    commit = result_commands.add_parser("commit")
    commit.add_argument("--attempt-id", required=True)
    commit.add_argument("--worker-id", required=True)
    commit.add_argument("--lease-token", required=True)
    commit.add_argument("--result-digest", required=True)
    commit.add_argument("--payload", type=Path, required=True)
    commit.add_argument("--failure-domain", choices=tuple(item.value for item in FailureDomain))
    _common_output(commit)
    commit.set_defaults(handler=_result_commit)
