# Security Policy

VeriRun executes code and environments that may be actively malicious. Security reports are taken seriously, including reports about sandbox escapes, policy bypasses, artifact handling, credential exposure, denial of service, and misleading security claims.

## Supported versions

VeriRun does not yet have a supported executable release. During pre-alpha development, security fixes are applied to the default branch and active release branch only.

Once releases begin, this table will identify supported versions:

| Version | Supported |
|---|---|
| Pre-alpha default branch | Best effort |
| Tagged executable releases | Not yet available |

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability.

Use GitHub's private vulnerability reporting flow:

1. Open the repository's **Security** tab.
2. Select **Report a vulnerability**.
3. Describe the affected revision, execution backend, environment, expected boundary, observed behavior, and a minimal reproduction.

If private vulnerability reporting is temporarily unavailable, use the private contact method listed on the maintainer's GitHub profile and disclose only enough public information to request a private channel.

Please include:

- the affected commit, tag, or image digest;
- operating system, container runtime, Kubernetes, gVisor, and policy versions where applicable;
- the trust boundary you believe was crossed;
- reproduction steps or a proof of concept;
- impact and prerequisites;
- whether the issue is already public.

You should receive an acknowledgement within seven days. Triage, remediation, disclosure timing, and credit will be coordinated through the private report.

## Scope

Security-relevant areas include:

- local, container, Kubernetes, and gVisor execution backends;
- artifact ingestion, extraction, paths, and retention;
- network, filesystem, process, CPU, memory, output, and time policies;
- API authentication and authorization when introduced;
- secrets in manifests, logs, traces, reports, or CI;
- dependency and container-image supply chain;
- result integrity, idempotent commit, and lineage tampering;
- denial-of-service paths that violate documented resource controls.

Benchmark inaccuracies without a security impact should use a regular bug report. Deliberate manipulation of result lineage or verifier policy may be security-relevant.

## Security claims

The development-only local executor is not a security boundary. A default container is not considered sufficient evidence for hostile multi-tenant execution. Kubernetes/gVisor claims apply only to the documented environment and attack suite; no backend is described as absolutely secure.

## Safe harbor

Good-faith research that avoids privacy violations, data destruction, service disruption, and access beyond what is necessary to demonstrate the issue will be treated as authorized security research for this project. Stop testing and report immediately if you encounter sensitive data or affect other users.
