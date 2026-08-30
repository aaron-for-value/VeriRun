# VeriRun v0.3 Kubernetes/gVisor Smoke Report

> This report records bounded Jobs in the declared Kubernetes context and
> RuntimeClass. It does not claim protection from runtime, kernel, image, CNI,
> control-plane, or operator compromise outside the tested controls.

- Kubernetes context: `kind-verirun-m2`
- Namespace: `verirun-m2-live`
- RuntimeClass: `gvisor`
- Image: `python@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31`
- Python: `3.12.13`
- Platform: `macOS-26.5.2-arm64-arm-64bit`
- Source revision: `4b7817e959a52d92b007ebc0dcdad07ef9998efd`
- Working tree clean at start: `True`

| Case | Expected | Baseline | Replay | Semantic match |
|---|---|---|---|---|
| kubernetes-pass-control | passed | passed | passed | yes |
| kubernetes-timeout | timeout | timeout | timeout | yes |
| kubernetes-output-flood | passed | passed | passed | yes |
| kubernetes-memory-pressure | oom | oom | oom | yes |
| kubernetes-no-egress | test_failure | test_failure | test_failure | yes |
| kubernetes-read-only-root | test_failure | test_failure | test_failure | yes |
| kubernetes-privilege-escalation | test_failure | test_failure | test_failure | yes |
| kubernetes-invalid-source | compile_error | compile_error | compile_error | yes |
| kubernetes-artifact-tamper | infra_error | infra_error | infra_error | yes |

## Attack coverage

- timeout, bounded output, memory pressure, and default-deny egress;
- root filesystem write and in-process privilege-escalation attempts;
- invalid source rejection before execution; and
- content-addressed candidate artifact tamper detection.

Each case is executed twice. Created Jobs are deleted by the executor; the
operator-provisioned namespace and its `default-deny-egress` policy remain
for the duration of the report.

## Residual risks

- Kubernetes has no portable per-Pod PID limit in this contract, so this report
  deliberately does not run a destructive fork bomb.
- Presence of `default-deny-egress` is preflighted; CNI enforcement is tested
  only by this report's bounded connection attempt.
