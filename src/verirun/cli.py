"""VeriRun v0.1 command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from verirun.artifacts import ArtifactStore
from verirun.canonical import content_hash, write_canonical_json
from verirun.evalplus_smoke import (
    DEFAULT_TASK_IDS,
    evalplus_smoke_succeeded,
    run_evalplus_smoke,
)
from verirun.executor import LocalExecutor
from verirun.fixtures import SmokeCase, build_smoke_manifest
from verirun.models import EvalManifest, VerificationResult, VerificationStatus
from verirun.replay import compare_results
from verirun.smoke import run_smoke, smoke_succeeded


def _load_manifest(path: Path) -> EvalManifest:
    return EvalManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _load_result(path: Path) -> VerificationResult:
    return VerificationResult.model_validate_json(path.read_text(encoding="utf-8"))


def _verify(args: argparse.Namespace) -> int:
    store = ArtifactStore(args.store)
    candidate = args.candidate.read_text(encoding="utf-8")
    tests = args.tests.read_text(encoding="utf-8")
    case = SmokeCase(
        name=args.candidate_id,
        task_id=args.task_id,
        candidate=candidate,
        tests=tests,
        expected_status=VerificationStatus.PASSED,
    )
    manifest = build_smoke_manifest(case, store)
    manifest = manifest.model_copy(
        update={
            "run_id": args.run_id,
            "verifier": manifest.verifier.model_copy(update={"timeout_seconds": args.timeout}),
        }
    )
    result = LocalExecutor().execute(manifest, store)
    args.output.mkdir(parents=True, exist_ok=True)
    write_canonical_json(args.output / "manifest.json", manifest)
    write_canonical_json(args.output / "result.json", result)
    print(f"status={result.status.value} result_hash={content_hash(result)}")
    return 0 if result.status is VerificationStatus.PASSED else 2


def _replay(args: argparse.Namespace) -> int:
    manifest = _load_manifest(args.manifest)
    baseline = _load_result(args.baseline)
    store = ArtifactStore(args.store)
    replay = LocalExecutor().execute(manifest, store)
    comparison = compare_results(baseline, replay)
    args.output.mkdir(parents=True, exist_ok=True)
    write_canonical_json(args.output / "replay.json", replay)
    write_canonical_json(args.output / "comparison.json", comparison)
    print(
        f"matched={str(comparison.matched).lower()} semantic_hash={comparison.replay_semantic_hash}"
    )
    return 0 if comparison.matched else 3


def _smoke(args: argparse.Namespace) -> int:
    summary = run_smoke(args.output)
    print(
        f"expected_statuses_matched={str(summary['expected_statuses_matched']).lower()} "
        f"semantic_replays_matched={str(summary['semantic_replays_matched']).lower()}"
    )
    return 0 if smoke_succeeded(summary) else 4


def _evalplus_smoke(args: argparse.Namespace) -> int:
    task_ids = tuple(args.task_id) if args.task_id else DEFAULT_TASK_IDS
    summary = run_evalplus_smoke(args.output, task_ids=task_ids)
    print(
        f"expected_outcomes_matched={str(summary['expected_outcomes_matched']).lower()} "
        f"semantic_replays_matched={str(summary['semantic_replays_matched']).lower()}"
    )
    return 0 if evalplus_smoke_succeeded(summary) else 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verirun")
    subparsers = parser.add_subparsers(dest="command", required=True)

    smoke = subparsers.add_parser("smoke", help="run trusted v0.1 synthetic fixtures twice")
    smoke.add_argument("--output", type=Path, default=Path("evidence/v0.1/synthetic"))
    smoke.set_defaults(handler=_smoke)

    evalplus_smoke = subparsers.add_parser(
        "evalplus-smoke", help="run the labeled v0.1 HumanEval+ compatibility subset"
    )
    evalplus_smoke.add_argument(
        "--task-id",
        action="append",
        default=None,
        help="task ID to include; may be repeated",
    )
    evalplus_smoke.add_argument("--output", type=Path, default=Path("evidence/v0.1/evalplus"))
    evalplus_smoke.set_defaults(handler=_evalplus_smoke)

    verify = subparsers.add_parser("verify", help="verify one trusted Python candidate")
    verify.add_argument("--candidate", type=Path, required=True)
    verify.add_argument("--tests", type=Path, required=True)
    verify.add_argument("--task-id", required=True)
    verify.add_argument("--candidate-id", required=True)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--timeout", type=float, default=2.0)
    verify.add_argument("--store", type=Path, default=Path(".verirun/artifacts"))
    verify.add_argument("--output", type=Path, default=Path(".verirun/latest"))
    verify.set_defaults(handler=_verify)

    replay = subparsers.add_parser("replay", help="replay a manifest and compare a result")
    replay.add_argument("--manifest", type=Path, required=True)
    replay.add_argument("--baseline", type=Path, required=True)
    replay.add_argument("--store", type=Path, required=True)
    replay.add_argument("--output", type=Path, default=Path(".verirun/replay"))
    replay.set_defaults(handler=_replay)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
