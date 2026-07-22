# Changelog

All notable VeriRun changes are documented here. The project follows [Semantic Versioning](docs/VERSIONING.md) once a release is tagged.

## Unreleased

### Added

- Immutable v0.1 manifest, attempt, artifact, verification, and replay records.
- Exported JSON Schemas kept in sync with the public Pydantic records by CI.
- Canonical JSON and SHA-256 content identity.
- Content-addressed local artifact storage with integrity checks.
- A development-only local executor for trusted fixtures with structured failure classification.
- `verify`, `replay`, `smoke`, and optional `evalplus-smoke` CLI workflows.
- EvalPlus v0.3.1 compatibility adapter and labeled HumanEval+ subset evidence workflow.
- Python 3.12 CI, static analysis, coverage, build, and synthetic replay gates.
- A manually dispatched Linux EvalPlus compatibility workflow with uploaded evidence.
- Source revision and working-tree provenance in generated evidence reports.

### Security

- The local executor is explicitly restricted to trusted fixtures and makes no hostile-code isolation claim.
