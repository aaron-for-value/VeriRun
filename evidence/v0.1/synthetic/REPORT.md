# VeriRun v0.1 Protocol Smoke Report

> This report uses trusted synthetic fixtures. It is not an EvalPlus score or
> sandbox security evidence.

- Python: `3.12.13`
- Platform: `macOS-26.5.2-arm64-arm-64bit`
- Source revision: `b30b11d2e3e1b20ad7c3b7e3df3f314ae7d6a64c`
- Working tree clean at start: `True`
- Cases: `5`
- Expected statuses matched: `True`
- Semantic replays matched: `True`

| Case | Expected | Baseline | Replay | Semantic match |
|---|---|---|---|---|
| oracle-plus | passed | passed | passed | yes |
| boundary-base | passed | passed | passed | yes |
| boundary-plus | test_failure | test_failure | test_failure | yes |
| compile-error | compile_error | compile_error | compile_error | yes |
| timeout | timeout | timeout | timeout | yes |

## Limitations

- The local executor runs only trusted fixtures and is not a security boundary.
- Synthetic cases validate protocol and replay behavior, not model capability.
- EvalPlus compatibility evidence is produced separately through the official
  adapter.
