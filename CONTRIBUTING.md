# Contributing to VeriRun

Thank you for helping build trustworthy executable evaluation and reward infrastructure.

VeriRun is currently in foundation and v0.1 development. Contracts may evolve quickly, but correctness, provenance, security boundaries, and honest claims are non-negotiable.

## Before opening a pull request

1. Search existing issues and the [roadmap](ROADMAP.md).
2. Open or claim an issue unless the change is a small documentation correction.
3. Agree on the problem, scope, non-scope, and acceptance evidence before substantial implementation work.
4. Do not run untrusted or model-generated code on your host machine.

Large design changes should begin as a design proposal issue. A technology choice is not, by itself, a project outcome; explain the measured problem it solves.

## Development setup

The supported v0.1 development environment uses Python 3.12 and a project-local environment:

```bash
conda create --prefix .venv python=3.12 pip -y
./.venv/bin/python -m pip install -r requirements/core-dev.lock.txt
./.venv/bin/python -m pip install -e . --no-deps
make check
```

EvalPlus is an optional benchmark dependency and is installed separately:

```bash
./.venv/bin/python -m pip install -r requirements/evalplus-v0.1.lock.txt
./.venv/bin/python -m pip install -e . --no-deps
make evalplus-smoke
```

The default smoke targets write ignored local artifacts under `.verirun/`. Maintainers use `make evidence-synthetic` and `make evidence-evalplus` only when intentionally refreshing reviewable milestone evidence from a clean revision.

Do not activate the local executor for untrusted or model-generated code. The synthetic smoke fixtures committed to this repository are the only intended v0.1 inputs for that backend.

## Contribution contract

Every implementation or experiment should state:

- the invariant or user/operator problem being addressed;
- scope and explicit non-scope;
- acceptance criteria;
- tests and evidence artifacts;
- compatibility or migration impact;
- security and failure-mode considerations;
- the roadmap milestone it advances.

## Pull requests

Pull requests must:

- link the issue they advance;
- explain the behavioral contract being changed;
- include tests proportional to the risk;
- update user, operator, or contributor documentation when relevant;
- identify new dependencies and compatibility changes;
- state what evidence was generated and how to reproduce it;
- avoid unsupported benchmark, security, reliability, or performance claims.

Draft pull requests are welcome for early feedback. Keep commits focused and avoid mixing unrelated cleanup with behavioral changes.

## Testing expectations

VeriRun uses multiple test layers as the runtime grows:

- unit tests for schemas, hashing, state transitions, and error classification;
- integration tests for adapters and artifact lineage;
- replay tests for deterministic semantic results;
- attack tests for execution backends;
- recovery tests for retries, leases, and idempotent commit;
- load and chaos tests for distributed releases.

The relevant checks must pass before a milestone is considered complete. A merged implementation without reproducible evidence remains in verification.

## Security and benchmark integrity

- Never commit credentials, private benchmark data, model API keys, or proprietary artifacts.
- Treat benchmark inputs, model output, archives, logs, and artifacts as untrusted.
- Do not weaken an isolation policy to make a test pass.
- Label subset results as subset results.
- Record workload version, prompt protocol, model revision, sampling configuration, verifier identity, and environment for every public result.
- Report suspected vulnerabilities privately according to [SECURITY.md](SECURITY.md).

## Documentation

Documentation is part of the product contract. Prefer precise statements with environment, version, and evidence over promotional language. Significant architecture choices should receive an ADR under `docs/adr/`.

## Conduct

Participation in VeriRun is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
