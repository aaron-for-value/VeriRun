from __future__ import annotations

from typing import Any

import pytest

from verirun.adapters import evalplus
from verirun.adapters.evalplus import EvalPlusApi
from verirun.canonical import sha256_bytes
from verirun.models import VerificationStatus


def fake_api(*, base: str = "pass", plus: str = "pass") -> EvalPlusApi:
    problems = {
        "HumanEval/0": {
            "task_id": "HumanEval/0",
            "prompt": "def answer():\n",
            "canonical_solution": "    return 42\n",
        }
    }

    def load(**_kwargs: object) -> dict[str, dict[str, Any]]:
        return problems

    def digest(**_kwargs: object) -> str:
        return "a" * 32

    def groundtruth(
        selected: dict[str, dict[str, Any]],
        cache_key: str,
        _special: object,
    ) -> dict[str, dict[str, Any]]:
        assert tuple(selected) == ("HumanEval/0",)
        assert len(cache_key) == 64
        return {"HumanEval/0": {"base": [42], "plus": [42]}}

    def check(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "base": (base, [base == "pass"]),
            "plus": (plus, [plus == "pass"]),
        }

    return EvalPlusApi(load, digest, load, digest, groundtruth, check, ())


def test_load_subset_qualifies_upstream_md5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evalplus, "_load_api", fake_api)

    subset = evalplus.load_subset("humaneval", ("HumanEval/0",), subset_label="unit-subset")

    assert subset.dataset_digest == f"md5:{'a' * 32}"
    assert subset.subset_digest.startswith("sha256:")
    assert subset.dataset_release == "v0.1.10"


@pytest.mark.parametrize(
    ("base", "plus", "expected"),
    [
        ("pass", "pass", VerificationStatus.PASSED),
        ("pass", "fail", VerificationStatus.TEST_FAILURE),
        ("pass", "timeout", VerificationStatus.TIMEOUT),
    ],
)
def test_evaluate_candidate_maps_status_without_inventing_compile_errors(
    monkeypatch: pytest.MonkeyPatch,
    base: str,
    plus: str,
    expected: VerificationStatus,
) -> None:
    monkeypatch.setattr(evalplus, "_load_api", lambda: fake_api(base=base, plus=plus))
    subset = evalplus.load_subset("humaneval", ("HumanEval/0",), subset_label="unit-subset")

    result = evalplus.evaluate_candidate(
        subset,
        task_id="HumanEval/0",
        candidate_id="candidate-0",
        solution="def answer():\n    return 42\n",
    )

    assert result.mapped_status is expected
    assert result.base.status == base
    assert result.plus.status == plus
    assert result.candidate_hash == sha256_bytes(b"def answer():\n    return 42\n")


def test_subset_rejects_unknown_and_duplicate_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evalplus, "_load_api", fake_api)

    with pytest.raises(ValueError, match="unique"):
        evalplus.load_subset(
            "humaneval",
            ("HumanEval/0", "HumanEval/0"),
            subset_label="duplicates",
        )
    with pytest.raises(ValueError, match="unknown"):
        evalplus.load_subset("humaneval", ("HumanEval/9",), subset_label="unknown")
