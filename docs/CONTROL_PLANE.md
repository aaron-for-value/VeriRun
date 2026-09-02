# Durable control plane

M3 implements VeriRun's v0.4 control-plane contract. It persists evaluation intent
and authoritative state in PostgreSQL, stores immutable payloads in an S3-compatible
object store, and lets retryable workers take over expired work without changing the
verification plan.

The implementation is pre-release. The checked-in
[recovery smoke](../evidence/v0.4/control-plane/REPORT.md) demonstrates one local
PostgreSQL 16 + MinIO environment; it is not a high-availability or production-scale
claim.

## Architecture and ownership

```mermaid
flowchart LR
    CLI[Typed Python API / CLI] --> CP[Control-plane state machine]
    CP --> PG[(PostgreSQL)]
    CP --> S3[S3-compatible artifact store]
    W1[Worker A] -->|claim / heartbeat / commit| CP
    W2[Worker B] -->|reclaim expired lease| CP
    PG --> P[VerificationPlan registry]
    PG --> R[Run / task / attempt / result]
    PG --> C[Immutable command receipts]
    S3 --> A[sha256/xx/digest objects]
```

PostgreSQL is authoritative for plan lifecycle, run and task state, lease ownership,
command idempotency, final-result uniqueness, cohort identity, and artifact metadata.
The object store is authoritative for artifact bytes. Artifact metadata records a
content digest and `s3://` URI; every read recomputes size and SHA-256.

The in-memory backend is a thread-safe reference implementation for fast state-machine
tests. It is not a durable deployment mode.

## Frozen verification plans

The deterministic compiler accepts only:

- `TaskSpec` and evaluation intent;
- the verifier catalog, including adapter/version/config/image digests;
- required input-evidence digests and policy revision;
- aggregation policy, comparison-cohort identity, candidate count, and concurrency.

Candidate contents, model identity, and already-produced output are deliberately not
compiler inputs. The resulting digest freezes the selected verifier graph, evidence
requirements, aggregation policy, and budget estimate before candidates are scheduled.

Plans move through `draft → validated → frozen → superseded|invalid`. `draft` can also
become `invalid`, as can `validated`. Only `frozen` plans can create runs or issue new
claims. Every run, task, lease, and final result carries the same plan digest; runs
against a changed digest receive a distinct effective cohort and cannot be aggregated
with the original cohort.

See [ADR 0003](adr/0003-frozen-verification-plans-and-durable-commit.md) and the
exported [plan schema](../schemas/verification-plan-record-v1.json).

## Durable state and concurrency

The first migration creates these durable records:

| Record | Database invariant |
|---|---|
| `verification_plans` | primary key `(plan_id, revision)` and immutable canonical digest |
| `comparison_cohorts` | one effective cohort per requested cohort and plan digest |
| `eval_runs` | foreign key to the exact plan ID, revision, and digest |
| `run_tasks` | one task/candidate identity per run |
| `attempt_leases` | explicit owner, opaque token, heartbeat, expiry, and terminal state |
| `final_results` | primary key `(run_id, task_id, candidate_id, verification_plan_digest)` |
| `command_receipts` | one caller idempotency key bound to one canonical intent digest |
| `artifact_metadata` | one metadata record per SHA-256 object identity |

Claims lock the run and plan, then select queued work with `FOR UPDATE SKIP LOCKED`.
Production calls use PostgreSQL's clock for lease timestamps. An expired active lease
is marked `expired`, its task is returned to `queued`, and the next attempt retains the
same plan digest. An old worker cannot heartbeat or commit after takeover.

Execution is at least once. Final results are effectively once: a byte-for-byte
equivalent retry of the same commit returns the existing record; a changed digest,
payload, failure domain, or attempt is rejected. PostgreSQL's composite primary key is
the final concurrency guard.

## CLI

Install the control-plane dependencies and provide credentials through environment
variables rather than checked-in files:

```bash
./.venv/bin/python -m pip install -e '.[control-plane]'
export VERIRUN_POSTGRES_DSN='postgresql://USER:PASSWORD@HOST:5432/verirun'
./.venv/bin/python -m verirun control migrate
```

Compile a deterministic draft plan from a
[`PlanCompileRequest`](../schemas/plan-compile-request-v1.json), then register and
freeze it:

```bash
./.venv/bin/python -m verirun control plan compile \
  --request plan-request.json --plan-id plan-2026-09 --revision 1 --output plan.json
./.venv/bin/python -m verirun control plan register --plan plan.json
./.venv/bin/python -m verirun control plan transition \
  --plan-id plan-2026-09 --revision 1 --target validated --reason 'schema and policy valid'
./.venv/bin/python -m verirun control plan transition \
  --plan-id plan-2026-09 --revision 1 --target frozen --reason 'comparison cohort approved'
```

Create and inspect runs with the exported
[`CreateRunCommand`](../schemas/create-run-command-v1.json):

```bash
./.venv/bin/python -m verirun control run create --command create-run.json
./.venv/bin/python -m verirun control run inspect --run-id RUN_ID
./.venv/bin/python -m verirun control run cancel \
  --run-id RUN_ID --idempotency-key COMMAND_ID
./.venv/bin/python -m verirun control run resume \
  --run-id RUN_ID --idempotency-key ANOTHER_COMMAND_ID
./.venv/bin/python -m verirun control run replay \
  --source-run-id RUN_ID --replay-run-id REPLAY_ID --idempotency-key REPLAY_COMMAND_ID
```

`control task` exposes claim, heartbeat, and reclaim. `control result commit` requires
the attempt owner, lease token, result digest, JSON payload, and optional classified
failure domain. Use `--help` at each level for the typed arguments.

## Recovery and operations

1. Apply `verirun control migrate` before serving traffic. Migrations 1–2 are
   idempotent; migration 2 backfills direct plan IDs on durable task, lease, and result
   records.
2. Back up PostgreSQL and the S3 bucket as one recovery set. Metadata without bytes,
   or bytes without authoritative metadata, is incomplete.
3. After a control-plane process restart, reconstruct `PostgresControlPlane` with the
   same DSN. No in-process queue is authoritative.
4. Run `control task reclaim` periodically. Reclaim is safe to repeat and ignores
   non-expired or terminal leases.
5. Resume only explicitly cancelled runs. Replay creates a new run while preserving
   source lineage, candidate hashes, and the exact frozen plan.
6. Monitor database availability and object-store integrity separately; classify
   failures as plan, model, user-code, verifier, sandbox, scheduler, or storage.

The live recovery exercise requires dedicated PostgreSQL and S3-compatible test
services:

```bash
docker compose -f deploy/m3/compose.yaml up --detach --wait
export VERIRUN_POSTGRES_DSN='postgresql://USER:PASSWORD@HOST:5432/verirun'
export VERIRUN_S3_ENDPOINT='HOST:9000'
export VERIRUN_S3_SERVER_IDENTITY='minio/minio@sha256:IMAGE_DIGEST'
export VERIRUN_S3_ACCESS_KEY='ACCESS_KEY'
export VERIRUN_S3_SECRET_KEY='SECRET_KEY'
make control-plane-smoke
export VERIRUN_TEST_POSTGRES_DSN="$VERIRUN_POSTGRES_DSN"
export VERIRUN_TEST_S3_ENDPOINT="$VERIRUN_S3_ENDPOINT"
export VERIRUN_TEST_S3_ACCESS_KEY="$VERIRUN_S3_ACCESS_KEY"
export VERIRUN_TEST_S3_SECRET_KEY="$VERIRUN_S3_SECRET_KEY"
make check
```

For the checked-in local Compose fixture, use
`postgresql://verirun:verirun-test-only@127.0.0.1:55432/verirun` and
`127.0.0.1:59000` with the test-only access key and secret declared in the Compose
file. Stop the services with `docker compose -f deploy/m3/compose.yaml down`; named
test data volumes are retained unless the operator explicitly removes them.

It reconstructs the control-plane client, expires and reclaims a lease, rejects the
old worker's late result, commits one authoritative result, round-trips a content-
addressed S3 object, splits a changed-plan cohort, and rejects mixed aggregation.

## Current limits

- No HTTP service, authentication/authorization layer, dashboard, or multi-tenant
  isolation is claimed; the typed Python service and CLI are the current interfaces.
- Migrations 1–2 create and backfill the current schema. A general upgrade/rollback
  migration framework is not yet published.
- PostgreSQL and the object store must be operated, secured, encrypted, backed up, and
  monitored by the deployer. The smoke uses test-only local credentials.
- No PostgreSQL failover, object-store outage chaos, sustained load, or multi-region
  evidence is included in M3.
- No exactly-once execution claim is made. Worker attempts may repeat.
- Ray/KubeRay scheduling, dynamic LLM planning, candidate-adaptive verifier selection,
  and dashboard work remain outside v0.4.
