from __future__ import annotations

from pathlib import Path

from verirun.cli import main
from verirun.smoke import run_smoke, smoke_succeeded


def test_smoke_workflow_generates_replay_evidence(tmp_path: Path) -> None:
    summary = run_smoke(tmp_path)

    assert smoke_succeeded(summary)
    assert (tmp_path / "summary.json").is_file()
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "not an EvalPlus score" in report
    assert "Semantic replays matched: `True`" in report
    for case in summary["cases"]:
        assert (tmp_path / "cases" / case["name"] / "comparison.json").is_file()


def test_cli_smoke_returns_success(tmp_path: Path) -> None:
    assert main(["smoke", "--output", str(tmp_path)]) == 0
