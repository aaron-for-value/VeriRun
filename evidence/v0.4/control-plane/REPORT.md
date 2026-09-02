# VeriRun M3 Durable Control Plane Smoke

- Succeeded: `true`
- PostgreSQL: `16.13 (Debian 16.13-1.pgdg13+1)`
- S3 server: `minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`
- Plan digest: `2f8feef11fe568effad85cff714eceb1a7947cc30507af327276fce13f6bb3ee`
- Changed plan digest: `4375059d6323cf9e7c98b69d58fc6e646e3529c8c963c7bbd9cf84ca4b7c87cd`
- Artifact digest: `e6ecca5823356825bd58b1734c1c0dc0b23cb8036f6819f5a0adc769b7eaef6b`
- Source revision: `59fbf7ec570a56448a4cf8ac450ec70134b5aeff`
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
