# Reliability and evaluation evidence

v0.6 adds an evidence contract around the existing execution protocol. It does not
turn VeriRun into a provider-SLO service or a model benchmark.

## Correlation boundary

`TelemetryRecorder` emits OpenTelemetry spans and a deterministic serializable event
record for the driver-side M4 path:

```text
run execute (scheduler)
  ├─ attempt submit (scheduler)
  ├─ attempt result (worker)
  └─ attempt commit (control-plane)
```

Each event carries the durable run, task, attempt and frozen verification-plan digest
when that identity is available. Artifact SHA-256 may be attached by an adapter that
has an artifact reference. OpenTelemetry data is observational: durable M3 records
remain the authority for lease and final-result state.

## Validity policy

`ReliabilityPolicy` is embedded in each reliability and paired-comparison report.
The default policy requires complete lineage, permits at most 5% infrastructure
exclusions, and requires 30 included paired tasks before a paired conclusion is
called sufficient.

| Condition | Report state |
|---|---|
| Missing required lineage | `invalid` |
| Infrastructure exclusions above policy | `partial` / paired conclusion `insufficient` |
| Fewer included paired tasks than policy | paired conclusion `insufficient` |
| All policy checks pass | `valid` / paired conclusion `sufficient` |

Raw rates are retained when a report is invalid or partial, but consumers must not
promote them to a model, runtime, or provider claim.

## Statistics

The paired report uses Wilson 95% confidence intervals for `pass@1`, the standard
without-replacement estimator for `pass@k`, and a deterministic paired bootstrap for
the `pass@1` delta. Paired tasks must have matching task IDs and equal per-task sample
counts. The policy and exclusion decision stay adjacent to the numerical result.

## Reproducing the reference

Install the pinned reference dependencies, then run:

```bash
python -m pip install -r requirements/distributed-executor-v0.5.lock.txt
python -m pip install -r requirements/reliability-evidence-v0.6.lock.txt
make reliability-smoke
```

The smoke executes the local CPU trusted-fixture M4 baseline and replay with driver
telemetry, repeats the established Ray recovery and logical-concurrency references,
and produces a fixed 40-task paired fixture for statistics-pipeline coverage. Its
outputs are written beneath `.verirun/evidence/v0.6/reliability-smoke/`; use
`make evidence-reliability` only from the clean release revision when refreshing
checked-in evidence.

## Support boundary

The reference is limited to the local single-node Ray path and CPU trusted fixtures.
It excludes real model generations, external providers, live GPU workloads, hardware
capacity certification, multi-node behavior, HA, and long-duration production SLOs.
The 1/2/4/8/16 values are logical scheduler settings, never a throughput promise.
