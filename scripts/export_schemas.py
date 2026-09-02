"""Export or verify the public JSON Schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from verirun.adapters.evalplus import EvalPlusTaskResult
from verirun.control_plane import (
    ArtifactMetadataRecord,
    AttemptLease,
    CreateRunCommand,
    EvalRunRecord,
    FinalResultRecord,
    PlanCompileRequest,
    RunInspection,
    VerificationPlan,
)
from verirun.gateway import GatewayConfig, GenerationRequest, GenerationResult
from verirun.models import EvalManifest, ReplayComparison, VerificationResult

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIRECTORY = ROOT / "schemas"
MODELS: dict[str, type[BaseModel]] = {
    "artifact-metadata-v1.json": ArtifactMetadataRecord,
    "attempt-lease-v1.json": AttemptLease,
    "create-run-command-v1.json": CreateRunCommand,
    "eval-run-v1.json": EvalRunRecord,
    "eval-manifest-v1.json": EvalManifest,
    "evalplus-task-result-v1.json": EvalPlusTaskResult,
    "gateway-config-v1.json": GatewayConfig,
    "generation-request-v1.json": GenerationRequest,
    "generation-result-v1.json": GenerationResult,
    "final-result-v1.json": FinalResultRecord,
    "plan-compile-request-v1.json": PlanCompileRequest,
    "replay-comparison-v1.json": ReplayComparison,
    "run-inspection-v1.json": RunInspection,
    "verification-result-v1.json": VerificationResult,
    "verification-plan-record-v1.json": VerificationPlan,
}


def render_schema(model: type[BaseModel]) -> str:
    return (
        json.dumps(
            model.model_json_schema(mode="validation"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def export_schemas(*, check: bool) -> int:
    stale: list[str] = []
    for filename, model in MODELS.items():
        destination = SCHEMA_DIRECTORY / filename
        expected = render_schema(model)
        if check:
            if not destination.is_file() or destination.read_text(encoding="utf-8") != expected:
                stale.append(filename)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(expected, encoding="utf-8")
    if stale:
        print(f"stale schemas: {', '.join(stale)}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return export_schemas(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
