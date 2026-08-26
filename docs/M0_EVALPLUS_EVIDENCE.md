# M0 EvalPlus Full-Workload Evidence

This document describes VeriRun's complete M0 benchmark-fixture gate. It is a
reproducibility and verifier-lineage exercise, not a model evaluation, leaderboard,
or hostile-code-sandbox claim.

The generated public entry point is
[`evidence/m0/evalplus/REPORT.md`](../evidence/m0/evalplus/REPORT.md). A valid
checked-in report must identify a clean source revision. A report produced from a
dirty worktree is useful for local preflight only and must be regenerated before a
merge or release claim.

## What runs

The fixed EvalPlus v0.3.1 adapter evaluates the complete ordered workloads:

| Dataset | Version | Standard tasks | Fixture recipes | Replays |
|---|---:|---:|---|---:|
| HumanEval+ | v0.1.10 | 164 | canonical source, `prompt + pass` | 2 |
| MBPP+ | v0.2.0 | 378 | canonical source, `prompt + pass` | 2 |

The report gives Base and Plus pass counts separately for every cohort. Five
additional boundary fixtures begin with canonical source and fail one input that is
present only in the pinned Plus set; all must be Base-pass / Plus-fail. This confirms
that the adapter preserves the distinction instead of collapsing it into one score.

`HumanEval/32` remains in the 164-task workload. Its pinned canonical source
reproducibly fails EvalPlus's `find_zero` residual verifier in both Base and Plus.
The report records it as an expected canonical-reference exception; it is neither
excluded nor replaced with a task-specific solver.

## Reproduce

Create an isolated Python 3.12 environment, install the pinned optional
environment, then run the complete gate:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements/evalplus-v0.1.lock.txt
.venv/bin/python -m pip install -e . --no-deps
EVALPLUS_MAX_MEMORY_BYTES=-1 .venv/bin/python -m verirun evalplus-m0 \
  --output evidence/m0/evalplus
```

On Darwin, `EVALPLUS_MAX_MEMORY_BYTES=-1` is required because EvalPlus v0.3.1's
memory rlimit path is incompatible with that environment. This setting is recorded
in the report and is not sandbox or resource-enforcement evidence.

The runner is resumable but rejects incomplete or different-candidate pairs. For a
time-bounded worker, run selections separately; these commands write only complete
task pairs and intentionally do not publish a final report:

```bash
EVALPLUS_MAX_MEMORY_BYTES=-1 .venv/bin/python -m verirun evalplus-m0 \
  --dataset humaneval --recipe oracle --skip-boundary
EVALPLUS_MAX_MEMORY_BYTES=-1 .venv/bin/python -m verirun evalplus-m0 \
  --dataset humaneval --recipe obvious-failure --skip-boundary
EVALPLUS_MAX_MEMORY_BYTES=-1 .venv/bin/python -m verirun evalplus-m0 \
  --dataset mbpp --recipe oracle --skip-boundary
EVALPLUS_MAX_MEMORY_BYTES=-1 .venv/bin/python -m verirun evalplus-m0 \
  --dataset mbpp --recipe obvious-failure --skip-boundary
EVALPLUS_MAX_MEMORY_BYTES=-1 .venv/bin/python -m verirun evalplus-m0 \
  --boundary-only
```

Re-run the complete command after every selection. It checks every matching pair and
then writes `manifest.json`, `summary.json`, and `REPORT.md`; only that complete
selection can return the M0 success gate.

## Audit layout

- `manifest.json` freezes the ordered task IDs, benchmark identities, fixture
  contract, source identity, and canonical-reference exception.
- `summary.json` contains cohort counts, Base-minus-Plus rates, result hashes, replay
  gates, and the Plus-only catch count.
- `cases/<dataset>/<task>/` contains baseline and replay records without candidate
  source. Each record carries the deterministic candidate SHA-256 instead.

The raw records are deliberately kept in the repository: they are small enough for
normal Git review and let a reader audit task-level statuses without trusting a
separate dashboard or expiring artifact link.
