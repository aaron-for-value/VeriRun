# VeriRun M3 Durable Control Plane Smoke

- Succeeded: `true`
- PostgreSQL: `16.13 (Debian 16.13-1.pgdg13+1)`
- S3 server: `minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`
- Plan digest: `21c3e216026c7840bd346453f9dee2a6c8871cbcb8ae16587a7efd18adf810e0`
- Changed plan digest: `e68010f7c98e067dbcc7f71c31fd619c93b6d8682cced59d084878c3b34707f2`
- Artifact digest: `926aa67891b869f5026abf796a1f97ca1b3671635761ca2e565f9eb193dd04dd`
- Source revision: `888f1957da2c9ebca2055bf17657ee9b6f85b55e`
- Source working tree clean: `true`

## Checks

- frozen_plan_only: `true`
- restart_recovered_state: `true`
- expired_lease_reclaimed: `true`
- takeover_kept_plan: `true`
- late_result_rejected: `true`
- duplicate_commit_effectively_once: `true`
- run_completed: `true`
- artifact_round_trip: `true`
- changed_plan_split_cohort: `true`
- mixed_aggregation_rejected: `true`
- failure_domains_complete: `true`

## Boundary

This smoke proves PostgreSQL persistence across control-plane client reconstruction, lease takeover, authoritative-result uniqueness, cohort splitting, and a live S3-compatible artifact round trip. It does not claim exactly-once execution.
