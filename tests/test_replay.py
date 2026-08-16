from __future__ import annotations

from pathlib import Path

from verirun.artifacts import ArtifactStore
from verirun.executor import LocalExecutor
from verirun.fixtures import build_smoke_manifest, smoke_cases
from verirun.replay import compare_results


def test_independent_attempts_match_semantically(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    manifest = build_smoke_manifest(smoke_cases()[0], store)
    baseline = LocalExecutor().execute(manifest, store, attempt_id="baseline")
    replay = LocalExecutor().execute(manifest, store, attempt_id="replay")

    comparison = compare_results(baseline, replay)

    assert comparison.matched is True
    assert comparison.differing_fields == ()
    assert comparison.baseline_result_hash != comparison.replay_result_hash
    assert comparison.baseline_semantic_hash == comparison.replay_semantic_hash


def test_different_candidate_does_not_match(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    baseline_manifest = build_smoke_manifest(smoke_cases()[0], store)
    replay_manifest = build_smoke_manifest(smoke_cases()[2], store)
    baseline = LocalExecutor().execute(baseline_manifest, store, attempt_id="baseline")
    replay = LocalExecutor().execute(replay_manifest, store, attempt_id="replay")

    comparison = compare_results(baseline, replay)

    assert comparison.matched is False
    assert "candidate_hash" in comparison.differing_fields
    assert "status" in comparison.differing_fields
