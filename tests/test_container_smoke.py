from __future__ import annotations

from pathlib import Path

from verirun.container_smoke import (
    container_smoke_markdown,
    container_smoke_succeeded,
    run_container_smoke,
)
from verirun.executor import ContainerExecutor, LocalExecutor


def test_container_smoke_success_requires_expected_statuses_and_replays() -> None:
    assert container_smoke_succeeded(
        {"expected_statuses_matched": True, "semantic_replays_matched": True}
    )
    assert not container_smoke_succeeded(
        {"expected_statuses_matched": True, "semantic_replays_matched": False}
    )


def test_container_smoke_report_states_the_development_claim_boundary() -> None:
    report = container_smoke_markdown(
        {
            "environment": {"python": "3.12", "platform": "test"},
            "container_image": "example.invalid/python@sha256:" + "a" * 64,
            "source": {"revision": "test", "working_tree_clean": True},
            "cases": [
                {
                    "name": "container-pass",
                    "expected_status": "passed",
                    "baseline_status": "passed",
                    "replay_status": "passed",
                    "comparison": {"matched": True},
                }
            ],
        }
    )

    assert "not Linux/Kubernetes/gVisor security evidence" in report
    assert "container-pass" in report


def test_container_smoke_writes_replayable_evidence_without_a_docker_daemon(
    monkeypatch, tmp_path: Path
) -> None:
    def execute_as_local(
        self: ContainerExecutor, manifest, store, *, attempt_id: str | None = None
    ):
        return LocalExecutor().execute(manifest, store, attempt_id=attempt_id)

    monkeypatch.setattr(ContainerExecutor, "execute", execute_as_local)
    monkeypatch.setattr(
        "verirun.container_smoke.source_state",
        lambda: {"revision": "clean-revision", "working_tree_clean": True, "source": "git"},
    )

    summary = run_container_smoke(
        tmp_path,
        image="example.invalid/python@sha256:" + "a" * 64,
    )

    assert container_smoke_succeeded(summary)
    assert summary["source"] == {
        "revision": "clean-revision",
        "working_tree_clean": True,
        "source": "git",
    }
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "REPORT.md").is_file()
    assert (tmp_path / "cases" / "container-timeout" / "comparison.json").is_file()
