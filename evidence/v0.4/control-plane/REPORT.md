# VeriRun M3 Durable Control Plane Smoke

- Succeeded: `true`
- PostgreSQL: `16.13 (Debian 16.13-1.pgdg13+1)`
- S3 server: `minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`
- Plan digest: `8718a101cc613c591c0204dffae6352f5920ff79814730f59615d732c5a951b3`
- Changed plan digest: `006ce9cbcf169d47dd1d7fb5b21c1fbd2e5818900ccc00758215f5d436837985`
- Artifact digest: `702f2845d623ae55834e93fa7789a2e54f9d3a33e940899479b6e2633a8c9dea`
- Source revision: `b65cffc9677c47380740f7cf95211a2578ea4264`
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
