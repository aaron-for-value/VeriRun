"""Best-effort source revision provenance for locally generated evidence."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def source_state() -> dict[str, str | bool]:
    explicit = os.environ.get("VERIRUN_SOURCE_REVISION")
    if explicit:
        return {"revision": explicit, "working_tree_clean": True, "source": "environment"}

    repository = Path(__file__).resolve().parents[2]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {"revision": "unknown", "working_tree_clean": False, "source": "unavailable"}
    return {
        "revision": revision,
        "working_tree_clean": not bool(status.strip()),
        "source": "git",
    }
