# VeriRun M3 Durable Control Plane Smoke

- Succeeded: `true`
- PostgreSQL: `16.13 (Debian 16.13-1.pgdg13+1)`
- S3 server: `quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`
- Plan digest: `e75b1d166df94c5e06b1dcd6355347722b8bc53bfc64dda37333c20d11623512`
- Changed plan digest: `0d663de941114111d2d8da8a95e0b1c3c2d3d7aa6bca35234706770ec49a97bb`
- Artifact digest: `b3c972d2dae993aa69e22591efe6bc9996bb68f6377ebcaaedcfdc276590a426`
- Source revision: `60d17298a2c2c0e728bfe72c7aaf34197646c931`
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
