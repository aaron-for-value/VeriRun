# VeriRun M0 EvalPlus full-workload evidence

> This is reproducibility evidence for deterministic fixtures, not a model score,
> leaderboard result, or hostile-code sandbox claim.

## Reproduction contract

- EvalPlus package: `v0.3.1` at `e5d0ed0bab96280b60b637ec7f15b5e4841b0cb2`
- Source revision: `555a176126363d401874d266dc00adf02818d6ed`
- Working tree clean at start: `False`
- Python: `3.12.13` on `macOS-26.5.2-arm64-arm-64bit`
- EvalPlus max memory bytes: `-1`
- Protocol: standard ordered workloads for oracle and obvious-failure fixtures;
  a separate five-task boundary-fixture cohort demonstrates Plus-only catches.

## Base / Plus aggregate results

| Dataset | Fixture | Tasks | Base pass | Plus pass | Base - Plus | Expected | Replay |
|---|---|---:|---:|---:|---:|---|---|
| `humaneval` | `oracle` | 164 | 163/164 (99.4%) | 163/164 (99.4%) | 0.0% | `True` | `True` |
| `humaneval` | `obvious-failure` | 164 | 0/164 (0.0%) | 0/164 (0.0%) | 0.0% | `True` | `True` |
| `mbpp` | `oracle` | 378 | 378/378 (100.0%) | 378/378 (100.0%) | 0.0% | `True` | `True` |
| `mbpp` | `obvious-failure` | 378 | 0/378 (0.0%) | 0/378 (0.0%) | 0.0% | `True` | `True` |

## Plus-only boundary fixtures

Each fixture starts from the official canonical solution, then raises only for one
input in the pinned Plus input set and absent from its Base input set.
The input itself is represented by a SHA-256 digest so the report does not duplicate
upstream test data.

| Task | Base | Plus | Plus-only input digest | Replay |
|---|---|---|---|---|
| `HumanEval/0` | `pass` | `fail` | `sha256:16f423a482263ca48a32624a61d936675c053f5dac2f13bc20421c8f7f17fcb7` | `True` |
| `HumanEval/1` | `pass` | `fail` | `sha256:6eee0a8f3630a4b46cca5723d7917339565626a9c85df5df50da6652a9cf5587` | `True` |
| `HumanEval/2` | `pass` | `fail` | `sha256:e78a2209ee27f28f438f29eac322ec492357cc74f6180497a97248f1c53ca183` | `True` |
| `Mbpp/2` | `pass` | `fail` | `sha256:792bfceb41f6923ca79d45cc90fc2ae5b2442855e0322bee64177097eecd1660` | `True` |
| `Mbpp/3` | `pass` | `fail` | `sha256:94fc4c0d0b097ac12a0897ab5fc530e301c9a88655de6506a0eb2bc90c653462` | `True` |

## Audit layout

- `manifest.json` freezes the ordered task sets, dataset digests, fixture contract,
  environment, and source identity.
- `summary.json` contains aggregate rates and hashes for every baseline/replay pair.
- `cases/<dataset>/<task>/` contains source-free structured EvalPlus records for
  each baseline and replay. Candidate source is deterministically reconstructed
  from the pinned dataset and its SHA-256 is recorded in each record.

## Limitations

- These fixtures validate adapter behavior and replay provenance, not capability.
- The local EvalPlus path runs only trusted, deterministic fixtures and is not a
  hostile-code isolation or resource-enforcement claim.
- On Darwin, this evidence uses `EVALPLUS_MAX_MEMORY_BYTES=-1` because the pinned
  EvalPlus release cannot apply its default memory rlimit there; this exception is
  recorded above and does not transfer to later Linux sandbox evidence.

## Canonical-reference exceptions

The task remains in the standard workload. The exception is versioned in the
manifest and source-free result records carry the resulting candidate hash.

- `humaneval/HumanEval/32`: The pinned canonical source fails the EvalPlus find_zero residual verifier; retain the task and report the reproducible failure.
