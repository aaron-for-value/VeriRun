from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from verirun import cli, evalplus_smoke
from verirun.adapters.evalplus import (
    EvalPlusPhaseResult,
    EvalPlusSubset,
    EvalPlusTaskResult,
)
from verirun.models import VerificationStatus


def test_evalplus_smoke_writes_labeled_replay_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    subset = EvalPlusSubset(
        dataset="humaneval",
        dataset_release="v0.1.10",
        dataset_digest=f"md5:{'a' * 32}",
        subset_label=evalplus_smoke.SUBSET_LABEL,
        subset_digest=f"sha256:{'b' * 64}",
        task_ids=("HumanEval/0",),
        problems={"HumanEval/0": {}},
        expected_outputs={"HumanEval/0": {}},
        check_correctness=lambda *_args, **_kwargs: {},
    )

    monkeypatch.setattr(evalplus_smoke, "load_subset", lambda *_args, **_kwargs: subset)
    monkeypatch.setattr(evalplus_smoke, "oracle_solution", lambda *_args: "oracle")
    monkeypatch.setattr(evalplus_smoke, "obvious_failure_solution", lambda *_args: "failure")

    def evaluate(
        _subset: EvalPlusSubset,
        *,
        task_id: str,
        candidate_id: str,
        solution: str,
    ) -> EvalPlusTaskResult:
        passed = solution == "oracle"
        raw_status = "pass" if passed else "fail"
        return EvalPlusTaskResult(
            dataset="humaneval",
            dataset_release="v0.1.10",
            dataset_digest=f"md5:{'a' * 32}",
            subset_label=evalplus_smoke.SUBSET_LABEL,
            subset_digest=f"sha256:{'b' * 64}",
            task_id=task_id,
            candidate_id=candidate_id,
            candidate_hash=("c" if passed else "d") * 64,
            base=EvalPlusPhaseResult(status=raw_status, details=(passed,)),
            plus=EvalPlusPhaseResult(status=raw_status, details=(passed,)),
            mapped_status=(
                VerificationStatus.PASSED if passed else VerificationStatus.TEST_FAILURE
            ),
        )

    monkeypatch.setattr(evalplus_smoke, "evaluate_candidate", evaluate)

    summary = evalplus_smoke.run_evalplus_smoke(
        tmp_path,
        task_ids=("HumanEval/0",),
    )

    assert evalplus_smoke.evalplus_smoke_succeeded(summary)
    assert len(summary["cases"]) == 2
    assert (tmp_path / "summary.json").is_file()
    assert "not a HumanEval+ score" in (tmp_path / "REPORT.md").read_text(encoding="utf-8")


def test_evalplus_smoke_cli_exit_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    summary: dict[str, Any] = {
        "expected_outcomes_matched": True,
        "semantic_replays_matched": True,
    }
    monkeypatch.setattr(cli, "run_evalplus_smoke", lambda *_args, **_kwargs: summary)

    assert cli.main(["evalplus-smoke", "--output", str(tmp_path)]) == 0

    summary["semantic_replays_matched"] = False
    assert cli.main(["evalplus-smoke", "--output", str(tmp_path)]) == 5
