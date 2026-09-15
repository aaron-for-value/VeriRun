# Distributed executor boundary

v0.5 (M4) adds a bounded Ray execution plane below the v0.4 durable control
plane. It is deliberately not a general agent-evaluation platform, and its
current live reference is CPU trusted fixtures only.

## Authority split

The control-plane driver remains the only component that changes durable
business state:

1. It reads a frozen run and claims one task through the v0.4 lease API.
2. It sends an immutable work item to Ray. The item includes the task/candidate
   identity, candidate hash, attempt ID, lease token, plan ID, and plan digest.
3. The Ray worker computes a result payload only. It has no control-plane
   client and cannot commit, alter a lease, or select a verifier.
4. The driver validates attempt and plan lineage, then performs the single
   v0.4 final-result commit.

Consequently, a Ray retry or worker replacement is an execution event, not a
business-idempotency mechanism. A worker failure leaves the active lease for
the v0.4 reclaim path; a fresh claim uses a new attempt ID. PostgreSQL remains
the authority that prevents duplicate final results.

## Work admission

`RayExecutionConfig` requires a `cpu_verifier` named resource and supports
independent `gpu_inference` and `external_api` pools. Each Ray task requests
one CPU plus the named resource selected for its work class. The driver keeps
no more than `max_in_flight` unresolved object references using `ray.wait`.

The named GPU/API pools define scheduling intent only in M4. The supported
live fixture supplies `verifier_cpu`; no M4 artifact may claim GPU throughput,
an external provider SLA, or hardware availability.

## Data and execution

Ray Data is used only to normalize and shard immutable rows derived from
already-created run tasks. Candidate source, verification-policy selection,
plan compilation, aggregation, and final commit stay outside Ray. Ray Core
executes the already-claimed work item. The full rationale is in
[ADR 0004](adr/0004-ray-data-and-core-execution-boundary.md).

## Current evidence boundary

The reference environment is the project's Python 3.12 environment, Ray
2.50.1, KubeRay v1.4.0, and the existing single-node `kind` cluster. It is a
correctness and recovery fixture environment, not capacity evidence. A
KubeRay `RayJob` will be treated as complete only after it runs the same
frozen-plan fixture, cleans up Ray infrastructure, and records its runtime
identity and replay comparison.

The local arm64 fixture image is built from `Dockerfile.ray-fixture`, whose
base is the arm64 digest for `python:3.12-slim`; it installs the reviewed
Ray Data/Core lock rather than inheriting a much larger general-purpose Ray
image. Its RayJob template
is `deploy/kuberay/verirun-m4-trusted-fixtures.rayjob.yaml`. The template uses
the valid local-only name `verirun-m4-ray-fixture:local` and `Never` pull
policy so it can be loaded into kind. Reproducible evidence must record the
resulting image digest and use that immutable reference in its captured
RayJob. It has
one named `verifier_cpu` worker resource, bounded pod resources, a 30-minute
active deadline, and KubeRay's legacy cleanup fields
`shutdownAfterJobFinishes: true` plus `ttlSecondsAfterFinished: 120`.

For the supported local kind reference, build and load the fixture image before
creating the generated-name RayJob:

```bash
docker build --platform linux/arm64 --file Dockerfile.ray-fixture \
  --build-arg VERIRUN_SOURCE_REVISION="$(git rev-parse HEAD)" \
  --tag verirun-m4-ray-fixture:local .
kind load docker-image --name verirun-m2 verirun-m4-ray-fixture:local
kubectl --context kind-verirun-m2 create \
  -f deploy/kuberay/verirun-m4-trusted-fixtures.rayjob.yaml
```

Capture the RayJob status and submitter logs before explicit cleanup. The
template's `generateName` deliberately creates a distinct job for each replay;
do not use `kubectl apply` for it. `ttlSecondsAfterFinished` shuts down the
RayCluster, but retained Completed submitter Pods and the RayJob CR must be
deleted explicitly after evidence capture. A release evidence run must additionally
capture `docker image inspect` output, the KubeRay operator and CRD versions,
Kubernetes node identity, and the two semantic summaries.

## Development fault and concurrency fixtures

`deploy/kuberay/verirun-m4-faults.rayjob.yaml` runs task and actor process crash,
transient final-commit, straggler, and large-object fixtures. The driver disables
Ray task retries and actor restarts; recovery must therefore occur through a new
v0.4 lease claim. The spill check reads the Ray Prometheus exporter, using the
larger of `ray_object_store_memory{Location="SPILLED"}` and
`ray_spill_manager_objects_bytes{State="Spilled"}` per node.

`distributed-concurrency` records logical 1/2/4/8/16 scheduling runs with a
fixed 20ms trusted fixture. Its throughput, P95 worker duration, driver RSS,
zero-spill, and zero-recovery rows are scheduler-fixture observations only; they
are not capacity, GPU, or provider performance evidence.

v0.5.0 release evidence records the completed live gates. Reproducing a different
environment remains development work until its own runtime identity, semantic
comparison, failure checks, and bounded cleanup are captured; it does not expand
the published distributed-execution support boundary.
