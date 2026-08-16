# VeriRun v0.1 Benchmark Protocol

This document defines the claims and reproducibility contract for VeriRun v0.1.

## Scope

v0.1 proves that trusted frozen candidates can be identified, verified, classified, stored, and replayed through immutable protocol records. It also provides an adapter to a fixed EvalPlus release without reimplementing EvalPlus's verifier semantics.

Machine-readable JSON Schemas for the public records are committed under `schemas/` and checked against the Pydantic source models in CI.

v0.1 does not prove secure execution of untrusted code, distributed execution, model quality, or production scale.

## Workload identity

Every run records:

- benchmark name, release or commit, split, and dataset hash;
- whether the run follows a standard protocol or a labeled subset protocol;
- ordered task IDs included in the run;
- candidate and test artifact SHA-256 identities;
- verifier adapter/version and execution policy;
- model and generation identity, including the explicit `frozen` model type when no model is called.

A subset run must include `subset_label` and `standard_protocol=false`. A subset result must never be presented as a full HumanEval+, MBPP+, or other standard benchmark score.

## EvalPlus compatibility

The v0.1 adapter targets:

- repository: `evalplus/evalplus`;
- release: `v0.3.1`;
- tag commit: `e5d0ed0bab96280b60b637ec7f15b5e4841b0cb2`;
- public data API: `get_human_eval_plus`, `get_human_eval_plus_hash`, `get_mbpp_plus`, and `get_mbpp_plus_hash`;
- public evaluation path: `get_groundtruth` and `check_correctness`.

The adapter preserves EvalPlus base/plus status and per-input details. VeriRun does not copy EvalPlus test logic or claim that EvalPlus's local reliability guard is a hostile-code security boundary.

Dataset hashes are captured from the EvalPlus API at run time and written to the evidence report with an explicit algorithm prefix. EvalPlus v0.3.1 exposes HumanEval+ and MBPP+ dataset identity as MD5, so records use `md5:<hex>`; VeriRun-owned artifacts and subset identities use `sha256:<hex>`. A benchmark name or package version without an algorithm-qualified dataset hash is incomplete provenance.

For EvalPlus v0.3.1, the bundled defaults are HumanEval+ `v0.1.10` and MBPP+ `v0.2.0`. The upstream HumanEval+ hash helper always resolves the bundled default release even when its `version` argument is set. VeriRun therefore records both the package release, declared dataset release, and returned digest instead of implying that the helper verified an arbitrary release argument.

## Candidate protocol

A candidate artifact contains complete Python source for the declared task. Frozen candidates use:

- `endpoint_type=frozen`;
- an explicit candidate ID;
- a content-addressed source artifact;
- deterministic generation metadata (`temperature=0`, `n=1`, and recorded seed).

Model-generated candidates will be added in v0.2. v0.1 never calls a model implicitly during replay.

## Verification statuses

Core structured statuses are:

- `passed`;
- `compile_error`;
- `test_failure`;
- `timeout`;
- `oom`;
- `policy_violation`;
- `infra_error`.

Backends may not silently convert infrastructure failures into test failures or zero reward. v0.1 reserves `oom` and `policy_violation` for backends that can identify those conditions reliably.

## Local executor boundary

The v0.1 local executor:

- is restricted to trusted development fixtures;
- compiles candidate and verifier source separately;
- executes a child interpreter with isolated Python flags;
- enforces wall-time and output-size bounds;
- kills the child process group after timeout;
- emits content-addressed stdout and stderr artifacts.

It is not a security boundary. It does not replace the container or Kubernetes/gVisor backends planned for v0.3.

On Darwin, EvalPlus v0.3.1 cannot apply its default memory `setrlimit` settings in the supported development environment. The built-in deterministic compatibility smoke may explicitly use `EVALPLUS_MAX_MEMORY_BYTES=-1`. The report records this setting, and the result is never security, isolation, or resource-enforcement evidence.

## Replay semantics

An independent attempt changes operational metadata such as:

- attempt ID;
- start and finish timestamps;
- measured duration;
- the full result record hash.

Semantic replay compares stable fields:

- run, task, and candidate identities;
- candidate and test hashes;
- mapped status and error classification;
- verifier version;
- exit code;
- stdout and stderr content hashes;
- output truncation state.

A replay matches only when all semantic fields match. Differing fields are listed explicitly.

## Synthetic smoke evidence

The trusted synthetic suite includes:

- an oracle implementation that passes base and boundary tests;
- a boundary-fragile implementation that passes base tests and fails extended tests;
- a syntax error;
- a non-terminating candidate that times out.

Each case runs twice. The report must show both expected status agreement and semantic replay agreement. This suite validates VeriRun protocol behavior; it is not an EvalPlus score.

## Publishing rules

Any published benchmark conclusion must include:

- VeriRun revision;
- Python and dependency versions;
- benchmark release/commit and dataset hash;
- standard or subset protocol label;
- task and candidate counts;
- candidate generation identity;
- verifier and execution policy;
- raw structured artifacts or immutable references;
- infrastructure exclusions and known limitations.

If infrastructure validity is unknown, the result is marked partial or invalid rather than presented as model capability evidence.
