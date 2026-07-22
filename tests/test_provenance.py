from __future__ import annotations

import subprocess

import pytest

from verirun.provenance import source_state


def test_source_revision_can_be_supplied_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VERIRUN_SOURCE_REVISION", "release-revision")

    assert source_state() == {
        "revision": "release-revision",
        "working_tree_clean": True,
        "source": "environment",
    }


def test_source_revision_reports_unavailable_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERIRUN_SOURCE_REVISION", raising=False)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(subprocess, "run", fail)

    assert source_state() == {
        "revision": "unknown",
        "working_tree_clean": False,
        "source": "unavailable",
    }
