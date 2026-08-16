"""Semantic replay comparison."""

from __future__ import annotations

from verirun.canonical import content_hash
from verirun.models import ReplayComparison, VerificationResult


def compare_results(baseline: VerificationResult, replay: VerificationResult) -> ReplayComparison:
    baseline_payload = baseline.semantic_payload()
    replay_payload = replay.semantic_payload()
    differing_fields = tuple(
        key
        for key in sorted(set(baseline_payload) | set(replay_payload))
        if baseline_payload.get(key) != replay_payload.get(key)
    )
    baseline_semantic_hash = content_hash(baseline_payload)
    replay_semantic_hash = content_hash(replay_payload)
    return ReplayComparison(
        baseline_result_hash=content_hash(baseline),
        replay_result_hash=content_hash(replay),
        baseline_semantic_hash=baseline_semantic_hash,
        replay_semantic_hash=replay_semantic_hash,
        matched=not differing_fields,
        differing_fields=differing_fields,
    )
