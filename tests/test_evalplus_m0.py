from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from verirun import cli, evalplus_m0
from verirun.adapters.evalplus import EvalPlusPhaseResult, EvalPlusSubset, EvalPlusTaskResult
from verirun.canonical import sha256_bytes
from verirun.models import VerificationStatus


def _subset() -> EvalPlusSubset:
    return EvalPlusSubset(
        dataset="humaneval",
        dataset_release="v0.1.10",
        dataset_digest=f"md5:{'a' * 32}",
        subset_label="unit-boundary",
        subset_digest=f"sha256:{'b' * 64}",
        task_ids=("HumanEval/0",),
        problems={
            "HumanEval/0": {
                "entry_point": "answer",
                "prompt": "def answer(value):\n",
                "canonical_solution": "    return value\n",
                "base_input": [[1]],
                "plus_input": [[1], [2]],
            }
        },
        expected_outputs={"HumanEval/0": {}},
        check_correctness=lambda *_args, **_kwargs: {},
    )


def test_plus_boundary_solution_targets_only_a_plus_input() -> None:
    source, input_digest = evalplus_m0.plus_boundary_solution(_subset(), "HumanEval/0")

    assert "def answer(*args, **kwargs):" in source
    assert "list(args) == [2]" in source
    assert input_digest.startswith("sha256:")


def test_canonical_exception_is_explicit_and_does_not_change_candidate_source() -> None:
    source, input_digest = evalplus_m0._oracle_factory(_subset(), "HumanEval/0")

    assert source == "def answer(value):\n    return value\n"
    assert input_digest is None
    assert evalplus_m0._expected_outcomes("humaneval", "HumanEval/32", "oracle") == (
        "fail",
        "fail",
    )


def test_m0_success_requires_expected_replay_and_five_catches() -> None:
    summary: dict[str, Any] = {
        "complete_selection": True,
        "expected_outcomes_matched": True,
        "semantic_replays_matched": True,
        "plus_boundary_catches": 5,
    }
    assert evalplus_m0.m0_evalplus_succeeded(summary)

    summary["plus_boundary_catches"] = 4
    assert not evalplus_m0.m0_evalplus_succeeded(summary)

    summary["plus_boundary_catches"] = 5
    summary["complete_selection"] = False
    assert evalplus_m0.m0_evalplus_selection_succeeded(summary)
    assert not evalplus_m0.m0_evalplus_succeeded(summary)


def test_m0_runner_resumes_only_a_matching_complete_pair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    subset = _subset()
    source, _input_digest = evalplus_m0._oracle_factory(subset, "HumanEval/0")
    result = EvalPlusTaskResult(
        dataset="humaneval",
        dataset_release="v0.1.10",
        dataset_digest=f"md5:{'a' * 32}",
        subset_label="unit-boundary",
        subset_digest=f"sha256:{'b' * 64}",
        task_id="HumanEval/0",
        candidate_id="m0:humaneval:HumanEval/0:oracle",
        candidate_hash=sha256_bytes(source.encode("utf-8")),
        base=EvalPlusPhaseResult(status="pass", details=(True,)),
        plus=EvalPlusPhaseResult(status="pass", details=(True, True)),
        mapped_status=VerificationStatus.PASSED,
    )
    evalplus_m0._write_case(tmp_path, result, result, recipe="oracle")
    monkeypatch.setattr(
        evalplus_m0,
        "evaluate_candidate",
        lambda *_args, **_kwargs: pytest.fail("a complete matching pair must be reused"),
    )

    records = evalplus_m0._run_candidate(
        tmp_path,
        subset,
        recipe="oracle",
        factory=evalplus_m0._oracle_factory,
    )

    assert len(records) == 1
    assert records[0]["replay_matched"]


def test_evalplus_m0_cli_exit_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    summary: dict[str, Any] = {
        "complete_selection": True,
        "expected_outcomes_matched": True,
        "semantic_replays_matched": True,
        "plus_boundary_catches": 5,
    }
    monkeypatch.setattr(cli, "run_m0_evalplus", lambda *_args, **_kwargs: summary)

    assert cli.main(["evalplus-m0", "--output", str(tmp_path)]) == 0

    summary["plus_boundary_catches"] = 4
    assert cli.main(["evalplus-m0", "--output", str(tmp_path)]) == 7
