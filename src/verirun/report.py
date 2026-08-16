"""Smoke and replay evidence reports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def smoke_markdown(summary: dict[str, Any]) -> str:
    rows: Sequence[dict[str, Any]] = summary["cases"]
    lines = [
        "# VeriRun v0.1 Protocol Smoke Report",
        "",
        "> This report uses trusted synthetic fixtures. It is not an EvalPlus score or",
        "> sandbox security evidence.",
        "",
        f"- Python: `{summary['environment']['python']}`",
        f"- Platform: `{summary['environment']['platform']}`",
        f"- Source revision: `{summary['source']['revision']}`",
        f"- Working tree clean at start: `{summary['source']['working_tree_clean']}`",
        f"- Cases: `{len(rows)}`",
        f"- Expected statuses matched: `{summary['expected_statuses_matched']}`",
        f"- Semantic replays matched: `{summary['semantic_replays_matched']}`",
        "",
        "| Case | Expected | Baseline | Replay | Semantic match |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {name} | {expected} | {baseline} | {replay} | {matched} |".format(
                name=row["name"],
                expected=row["expected_status"],
                baseline=row["baseline_status"],
                replay=row["replay_status"],
                matched="yes" if row["comparison"]["matched"] else "no",
            )
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- The local executor runs only trusted fixtures and is not a security boundary.",
            "- Synthetic cases validate protocol and replay behavior, not model capability.",
            "- EvalPlus compatibility evidence is produced separately through the official",
            "  adapter.",
            "",
        ]
    )
    return "\n".join(lines)
