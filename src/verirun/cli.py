"""VeriRun command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from verirun.artifacts import ArtifactStore
from verirun.canonical import content_hash, write_canonical_json
from verirun.container_smoke import container_smoke_succeeded, run_container_smoke
from verirun.evalplus_m0 import (
    STANDARD_DATASETS,
    STANDARD_RECIPES,
    m0_evalplus_selection_succeeded,
    m0_evalplus_succeeded,
    run_m0_evalplus,
)
from verirun.evalplus_smoke import (
    DEFAULT_TASK_IDS,
    evalplus_smoke_succeeded,
    run_evalplus_smoke,
)
from verirun.executor import executor_for
from verirun.fixtures import SmokeCase, build_smoke_manifest
from verirun.gateway_smoke import gateway_smoke_succeeded, run_gateway_smoke
from verirun.kubernetes_smoke import kubernetes_smoke_succeeded, run_kubernetes_smoke
from verirun.models import EvalManifest, ExecutionSpec, VerificationResult, VerificationStatus
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
    execution = ExecutionSpec()
    verifier_updates: dict[str, object] = {"timeout_seconds": args.timeout}
    if args.engine in {"container", "kubernetes"}:
        image = args.container_image
        if image is None:
            raise ValueError("--container-image is required with an isolated engine")
        if args.engine == "container":
            execution = ExecutionSpec(
                engine="container",
                sandbox_policy="development-container",
                container_image=image,
                container_cpus=args.container_cpus,
                container_memory_mb=args.container_memory_mb,
                container_pids_limit=args.container_pids_limit,
            )
        else:
            required = {
                "--kubernetes-context": args.kubernetes_context,
                "--kubernetes-namespace": args.kubernetes_namespace,
                "--kubernetes-runtime-class": args.kubernetes_runtime_class,
            }
            missing = [flag for flag, value in required.items() if value is None]
            if missing:
                raise ValueError(f"{' '.join(missing)} is required with --engine kubernetes")
            execution = ExecutionSpec(
                engine="kubernetes",
                sandbox_policy="kubernetes-gvisor",
                container_image=image,
                container_cpus=args.container_cpus,
                container_memory_mb=args.container_memory_mb,
                kubernetes_context=args.kubernetes_context,
                kubernetes_namespace=args.kubernetes_namespace,
                kubernetes_runtime_class=args.kubernetes_runtime_class,
            )
        verifier_updates["image_digest"] = image.rsplit("@", maxsplit=1)[1]
    manifest = manifest.model_copy(
        update={
            "run_id": args.run_id,
            "verifier": manifest.verifier.model_copy(update=verifier_updates),
            "execution": execution,
        }
    )
    result = executor_for(manifest).execute(manifest, store)
    args.output.mkdir(parents=True, exist_ok=True)
    write_canonical_json(args.output / "manifest.json", manifest)
    write_canonical_json(args.output / "result.json", result)
    print(f"status={result.status.value} result_hash={content_hash(result)}")
    return 0 if result.status is VerificationStatus.PASSED else 2


def _replay(args: argparse.Namespace) -> int:
    manifest = _load_manifest(args.manifest)
    baseline = _load_result(args.baseline)
    store = ArtifactStore(args.store)
    replay = executor_for(manifest).execute(manifest, store)
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


def _evalplus_m0(args: argparse.Namespace) -> int:
    if args.boundary_only and (args.dataset or args.recipe):
        raise ValueError("--boundary-only cannot be combined with --dataset or --recipe")
    datasets = tuple(args.dataset) if args.dataset else STANDARD_DATASETS
    recipes = tuple(args.recipe) if args.recipe else STANDARD_RECIPES
    summary = run_m0_evalplus(
        args.output,
        datasets=() if args.boundary_only else datasets,
        recipes=() if args.boundary_only else recipes,
        include_boundary=args.boundary_only or not args.skip_boundary,
    )
    selected_succeeded = m0_evalplus_selection_succeeded(summary)
    print(
        f"m0_evalplus_selection_succeeded={str(selected_succeeded).lower()} "
        f"m0_evalplus_succeeded={str(m0_evalplus_succeeded(summary)).lower()} "
        f"plus_boundary_catches={summary['plus_boundary_catches']} "
        f"complete_selection={str(summary['complete_selection']).lower()}"
    )
    succeeded = (
        m0_evalplus_succeeded(summary) if summary["complete_selection"] else selected_succeeded
    )
    return 0 if succeeded else 7


def _gateway_smoke(args: argparse.Namespace) -> int:
    summary = run_gateway_smoke(args.output)
    backpressure = summary["backpressure"]
    print(
        f"gateway_smoke_succeeded={str(gateway_smoke_succeeded(summary)).lower()} "
        f"max_active={backpressure['max_fake_server_active_requests']}"
    )
    return 0 if gateway_smoke_succeeded(summary) else 6


def _container_smoke(args: argparse.Namespace) -> int:
    summary = run_container_smoke(args.output, image=args.image)
    print(
        f"container_smoke_succeeded={str(container_smoke_succeeded(summary)).lower()} "
        f"image={summary['container_image']}"
    )
    return 0 if container_smoke_succeeded(summary) else 8


def _kubernetes_smoke(args: argparse.Namespace) -> int:
    summary = run_kubernetes_smoke(
        args.output,
        image=args.image,
        context=args.kubernetes_context,
        namespace=args.kubernetes_namespace,
        runtime_class=args.kubernetes_runtime_class,
    )
    print(
        f"kubernetes_smoke_succeeded={str(kubernetes_smoke_succeeded(summary)).lower()} "
        f"runtime_class={summary['kubernetes_runtime_class']}"
    )
    return 0 if kubernetes_smoke_succeeded(summary) else 9


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

    evalplus_m0 = subparsers.add_parser(
        "evalplus-m0", help="run the complete M0 EvalPlus fixture and replay evidence"
    )
    evalplus_m0.add_argument("--output", type=Path, default=Path("evidence/m0/evalplus"))
    evalplus_m0.add_argument(
        "--dataset", choices=STANDARD_DATASETS, action="append", help="resume one dataset"
    )
    evalplus_m0.add_argument(
        "--recipe", choices=STANDARD_RECIPES, action="append", help="resume one fixture recipe"
    )
    evalplus_m0.add_argument(
        "--skip-boundary", action="store_true", help="omit the boundary-fixture cohort"
    )
    evalplus_m0.add_argument(
        "--boundary-only", action="store_true", help="run only the five boundary fixtures"
    )
    evalplus_m0.set_defaults(handler=_evalplus_m0)

    gateway_smoke = subparsers.add_parser(
        "gateway-smoke", help="run v0.2 local fake-server gateway fault scenarios"
    )
    gateway_smoke.add_argument("--output", type=Path, default=Path("evidence/v0.2/gateway-smoke"))
    gateway_smoke.set_defaults(handler=_gateway_smoke)

    container_smoke = subparsers.add_parser(
        "container-smoke", help="run v0.3 development-container runtime scenarios twice"
    )
    container_smoke.add_argument(
        "--image",
        required=True,
        help="pre-pulled digest-pinned Python image, for example repo@sha256:<digest>",
    )
    container_smoke.add_argument(
        "--output", type=Path, default=Path("evidence/v0.3/container-smoke")
    )
    container_smoke.set_defaults(handler=_container_smoke)

    kubernetes_smoke = subparsers.add_parser(
        "kubernetes-smoke", help="run v0.3 Kubernetes/gVisor attack scenarios twice"
    )
    kubernetes_smoke.add_argument("--image", required=True, help="digest-pinned Python image")
    kubernetes_smoke.add_argument("--kubernetes-context", required=True)
    kubernetes_smoke.add_argument("--kubernetes-namespace", required=True)
    kubernetes_smoke.add_argument("--kubernetes-runtime-class", required=True)
    kubernetes_smoke.add_argument(
        "--output", type=Path, default=Path("evidence/v0.3/kubernetes-smoke")
    )
    kubernetes_smoke.set_defaults(handler=_kubernetes_smoke)

    verify = subparsers.add_parser("verify", help="verify one Python candidate in a declared tier")
    verify.add_argument("--candidate", type=Path, required=True)
    verify.add_argument("--tests", type=Path, required=True)
    verify.add_argument("--task-id", required=True)
    verify.add_argument("--candidate-id", required=True)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--timeout", type=float, default=2.0)
    verify.add_argument("--engine", choices=("local", "container", "kubernetes"), default="local")
    verify.add_argument(
        "--container-image",
        help="digest-pinned image required for container tier, for example repo@sha256:<digest>",
    )
    verify.add_argument("--container-memory-mb", type=int, default=256)
    verify.add_argument("--container-cpus", type=float, default=1.0)
    verify.add_argument("--container-pids-limit", type=int, default=64)
    verify.add_argument("--kubernetes-context")
    verify.add_argument("--kubernetes-namespace")
    verify.add_argument("--kubernetes-runtime-class")
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
