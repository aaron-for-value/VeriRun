"""VeriRun protocol and execution runtime."""

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
    "BenchmarkSpec",
    "Candidate",
    "EvalManifest",
    "ExecutionSpec",
    "GenerationSpec",
    "ModelSpec",
    "ReplayComparison",
    "TaskAttempt",
    "VerificationResult",
    "VerificationStatus",
    "VerifierSpec",
]

__version__ = "0.1.0.dev0"
