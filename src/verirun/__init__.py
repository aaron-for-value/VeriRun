"""VeriRun protocol and execution runtime."""

from verirun.gateway import (
    AsyncModelGateway,
    GatewayConfig,
    GatewayErrorClass,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
)
from verirun.models import (
    ArtifactRef,
    BenchmarkSpec,
    Candidate,
    EvalManifest,
    ExecutionSpec,
    GenerationSpec,
    ModelSpec,
    ReplayComparison,
    TaskAttempt,
    VerificationResult,
    VerificationStatus,
    VerifierSpec,
)

__all__ = [
    "ArtifactRef",
    "AsyncModelGateway",
    "BenchmarkSpec",
    "Candidate",
    "EvalManifest",
    "ExecutionSpec",
    "GatewayConfig",
    "GatewayErrorClass",
    "GenerationRequest",
    "GenerationResult",
    "GenerationSpec",
    "GenerationStatus",
    "ModelSpec",
    "ReplayComparison",
    "TaskAttempt",
    "VerificationResult",
    "VerificationStatus",
    "VerifierSpec",
]

__version__ = "0.2.0.dev0"
