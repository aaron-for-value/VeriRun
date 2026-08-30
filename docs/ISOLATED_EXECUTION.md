# Isolated execution tiers

v0.3 is in development. This document records what the current container tier
does, the evidence it has, and the stronger evidence it does not claim.

## Tiers and claim boundary

| Tier | Manifest declaration | Intended use | Security claim |
|---|---|---|---|
| Local | `local` / `trusted-fixtures-only` | Deterministic trusted fixtures | None; it executes a host subprocess. |
| Development container | `container` / `development-container` | Local integration and attack-regression development | Operational risk reduction only. It is not gVisor evidence. |
| Kubernetes + gVisor | `kubernetes` / `kubernetes-gvisor` | Isolated Job execution | Implemented contract; no stronger security claim until clean Linux evidence and attack results are published. |

macOS and a default Docker runtime must never be presented as Kubernetes or gVisor
security evidence.

## Development-container contract

`container` manifests require all of the following immutable inputs:

- an image reference pinned as `repository@sha256:<64 hex>`;
- positive CPU, memory, and PID limits;
- `sandbox_policy: development-container`.

The executor rejects local manifests that carry container settings. It invokes
Docker with `--pull=never`, `--network none`, `--read-only`, a read-only bind mount
for the generated runner, numeric non-root user `65534:65534`, `--cap-drop ALL`,
`no-new-privileges`, memory/PID limits, and one CPU. `--pull=never` means a replay
cannot silently obtain a different image; operators must pre-pull the exact digest.
The generated runner lives in the caller's current workspace rather than the host
temporary directory, because VM-backed Docker products such as Colima may not share
macOS temporary paths with the daemon. It is removed after every attempt.

On wall-time expiry VeriRun kills the named container, terminates the Docker client,
and removes the container. A failed kill/remove operation is classified as
`infra_error/container_cleanup_error`, never as a successful verification. Docker's
reported `OOMKilled` state maps to `oom/memory_limit_exceeded`; other runtime exits
remain infrastructure failures unless the harness reported a test outcome.

## Threat model and residual risk

| Attack / failure class | Development-container control | Current automated regression | Residual risk / future gate |
|---|---|---|---|
| Infinite loop | VeriRun wall timeout, named-container kill and remove | Unit tests plus `container-smoke` runtime timeout/replay | Docker daemon/API availability can still delay cleanup. |
| Output flood | Bounded stdout/stderr artifact truncation | Existing executor truncation regression | A process may consume resources before its output is collected. |
| Memory exhaustion | Manifest memory limit and Docker `OOMKilled` mapping | OOM mapping regression | Requires Linux runtime evidence for enforcement. |
| Fork/process bomb | Docker PID limit | Command-contract regression | Requires live attack validation; PID limits are not a complete isolation boundary. |
| Network exfiltration | `--network none` | Command contract plus `container-smoke` no-network/replay | Does not protect against host-side daemon/image compromise. |
| Host/workspace write | Read-only root and read-only `/work` mount | Command contract plus `container-smoke` read-only/replay | Docker file-sharing and daemon configuration remain trusted. |
| Privilege escalation | Numeric non-root user, dropped capabilities, no-new-privileges | Command-contract regression | Kernel/runtime vulnerabilities are out of scope for this tier. |
| Residual containers/processes | Unique name plus forced remove; cleanup failure is fatal | Cleanup-failure regression | Requires Kubernetes Job/retry and process-leak evidence. |
| Kubernetes retry duplication | Job `backoffLimit: 0`, `completions: 1`, unique name, forced delete | Kubernetes Job contract and cleanup regressions | Durable final-result idempotency belongs to M3 control plane. |
| Kubernetes log outage | Three bounded log attempts; unavailable stream is an infra error | Kubernetes unavailable-log regression | A healthy kubelet log API is required for successful evidence. |
| Kubernetes network policy absent | Preflight requires `default-deny-egress` by name | Kubernetes missing-policy regression | Policy presence is not proof of CNI enforcement; live attack evidence remains required. |

Trusted components for this tier are the Docker daemon, selected digest-pinned image,
host kernel, host filesystem sharing, and VeriRun itself. Candidate source and tests
are untrusted. This trust boundary is why the roadmap still requires a restricted
Kubernetes Job backend, default-deny network policy, artifact allowlists, bounded
logs, and a versioned gVisor RuntimeClass environment before a stronger claim.

## Kubernetes + gVisor contract

`kubernetes` manifests require a digest-pinned image, CPU and memory limits, an
explicit Kubernetes context and namespace, and an explicit `RuntimeClass`. They
require `sandbox_policy: kubernetes-gvisor`; a Docker PID limit is deliberately not
accepted because Kubernetes does not expose that as a portable per-Pod resource.
The verifier image digest must exactly match the manifest image digest.

Before a Job is created, the executor verifies that the declared namespace already
has a `default-deny-egress` `NetworkPolicy`. The operator owns namespace provisioning
and the policy's enforcement-capable CNI. The Job is pinned to one completion and
zero backoff retries, uses a unique name, `activeDeadlineSeconds`, bounded CPU/memory,
non-root UID/GID 65534, dropped capabilities, `RuntimeDefault` seccomp, a read-only
root filesystem, no privilege escalation, and no mounted host paths or service-account
token. Candidate and verifier source are verified ArtifactStore inputs embedded in the
read-only runner; the only returned artifacts are bounded Kubernetes logs and the
structured result.

VeriRun always deletes a created Job after collecting its terminal state and logs. A
failed deletion is `infra_error/kubernetes_cleanup_error`. Log collection is retried
three times with a short bound; a missing or unavailable log stream is
`infra_error/kubernetes_logs_error` for a would-be successful Job, never a passing
result. If Kubernetes has already published `DeadlineExceeded` for the Pod, VeriRun
keeps the classified `timeout` result and stores the stable log-error marker instead
of pretending that the timeout passed. This preserves the rule that a successful
verification must be auditable. A clean Linux/Kubernetes report is still required
before this tier is cited as security evidence.

## Kubernetes/gVisor runtime recipe

Use a Linux Kubernetes node with `runsc` and `containerd-shim-runsc-v1` installed,
the `io.containerd.runsc.v1` handler registered, and a matching `RuntimeClass` (for
example, `gvisor`). The target namespace must be labeled for restricted Pod Security
admission and contain an enforced `default-deny-egress` NetworkPolicy before running
VeriRun; the CLI deliberately does not create either boundary.

Run the local, ignored evidence workflow with explicit identity inputs:

```bash
VERIRUN_CONTAINER_IMAGE='python@sha256:<64-hex-digest>' \
VERIRUN_KUBERNETES_CONTEXT='kind-verirun-m2' \
VERIRUN_KUBERNETES_NAMESPACE='verirun-m2-live' \
VERIRUN_KUBERNETES_RUNTIME_CLASS='gvisor' \
make kubernetes-smoke
```

The report records one pass control and eight bounded attack probes twice: deadline
timeout, output flood, memory pressure, egress, root filesystem write, in-process
privilege escalation, invalid source, and content-addressed artifact tamper. A live
fork bomb is intentionally excluded because this portable Kubernetes contract has no
per-Pod PID limit. Jobs are deleted after each attempt; the namespace and its policy
remain operator-owned. The local output is under
`.verirun/evidence/v0.3/kubernetes-smoke/`. Use `make evidence-kubernetes` only from
a clean revision when refreshing public evidence.

## Local development recipe

Pre-pull the exact image reference through the normal operator workflow, then pass
the same digest to `verify`:

```bash
docker pull repository@sha256:<64-hex-digest>
./.venv/bin/python -m verirun verify \
  --candidate candidate.py --tests tests.py \
  --task-id example/0 --candidate-id example --run-id local-container-1 \
  --engine container \
  --container-image repository@sha256:<64-hex-digest> \
  --container-cpus 1.0 --container-memory-mb 256 --container-pids-limit 64
```

The produced manifest freezes the tier, image digest, and CPU/memory/PID limits. This
recipe is for development-container evidence only; it does not satisfy the v0.3 exit
gate or establish a production sandbox guarantee.

To produce the four-case, two-replay runtime report, run:

```bash
VERIRUN_CONTAINER_IMAGE='repository@sha256:<64-hex-digest>' make container-smoke
```

The result is written below `.verirun/evidence/v0.3/container-smoke/`. Use
`make evidence-container` with the same variable only when intentionally refreshing
checked-in evidence from a clean revision.
