# VeriRun v0.6 Reliability Evidence Report

> This report proves telemetry, policy invalidation, and report mechanics on CPU
> trusted fixtures. It is not a model score, provider SLO, hardware-capacity, or
> production-reliability claim.

- Python: `3.12.13`
- Platform: `macOS-26.6.2-arm64-arm-64bit`
- Source revision: `714e77d83ca17dcc4a62a00b185dd366f0da41d0`
- Working tree clean at start: `True`
- Reliability validity: `valid`
- Paired-fixture conclusion: `sufficient`

## Contract checks

| Check | Result |
|---|---|
| baseline_completed | pass |
| replay_completed | pass |
| telemetry_captures_driver_path | pass |
| frozen_plan_replayed | pass |
| reliability_policy_valid | pass |
| paired_fixture_policy_sufficient | pass |
| fault_recovery_contract | pass |
| logical_capacity_reference_complete | pass |

## Fault-reference checks

| Check | Result |
|---|---|
| task_crash_recovered | pass |
| actor_crash_recovered | pass |
| storage_transient_recovered | pass |
| straggler_completed | pass |
| large_object_payloads_returned | pass |
| spill_metrics_scraped | fail |
| spill_observed | fail |

## Limitations

- The paired outcomes are fixed fixtures that validate the statistical pipeline only.
- The 1/2/4/8/16 matrix is a logical scheduler reference, not a CPU capacity result.
- Fault recovery is restricted to the local single-node Ray reference; no provider,
  multi-node, HA, GPU, or external-model behavior is inferred.
- A missing lineage, excessive infrastructure exclusion, or insufficient paired
  sample count is marked invalid, partial, or insufficient by the policy.
