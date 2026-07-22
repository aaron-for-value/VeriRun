from __future__ import annotations

from pathlib import Path

import pytest

from verirun.artifacts import ArtifactIntegrityError, ArtifactStore


def test_artifact_round_trip_and_deduplication(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    first = store.put_text(kind="candidate", text="answer")
    second = store.put_text(kind="candidate", text="answer")

    assert first == second
    assert store.read_text(first) == "answer"


def test_tampered_artifact_is_rejected(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    reference = store.put_text(kind="candidate", text="answer")
    (tmp_path / reference.relative_path).write_text("tampered", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="mismatch"):
        store.read_text(reference)


def test_missing_artifact_is_rejected(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    reference = store.put_text(kind="candidate", text="answer")
    (tmp_path / reference.relative_path).unlink()

    with pytest.raises(ArtifactIntegrityError, match="missing artifact"):
        store.read_text(reference)
