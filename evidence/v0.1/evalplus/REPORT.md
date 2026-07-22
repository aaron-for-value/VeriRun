# VeriRun v0.1 EvalPlus compatibility smoke

> This is a labeled three-task compatibility smoke, not a HumanEval+ score,
> leaderboard result, or hostile-code sandbox claim.

- EvalPlus package: `v0.3.1`
- Dataset release: `v0.1.10`
- Dataset digest: `md5:fe585eb4df8c88d844eeb463ea4d0302`
- Subset: `verirun-v0.1-humaneval-plus-smoke-3`
- Subset digest: `sha256:d8c367c9e2e7d3365f263e4c5848e567fad0270670f64dc5e8af7747cf5311a9`
- Source revision: `b30b11d2e3e1b20ad7c3b7e3df3f314ae7d6a64c`
- Working tree clean at start: `True`
- EvalPlus max memory bytes: `-1`
- Expected outcomes matched: `true`
- Semantic replays matched: `true`

| Task | Candidate recipe | Base | Plus | Mapped | Replay |
|---|---|---|---|---|---|
| `HumanEval/0` | `prompt+canonical_solution` | `pass` | `pass` | `passed` | `true` |
| `HumanEval/0` | `prompt+pass` | `fail` | `fail` | `test_failure` | `true` |
| `HumanEval/1` | `prompt+canonical_solution` | `pass` | `pass` | `passed` | `true` |
| `HumanEval/1` | `prompt+pass` | `fail` | `fail` | `test_failure` | `true` |
| `HumanEval/2` | `prompt+canonical_solution` | `pass` | `pass` | `passed` | `true` |
| `HumanEval/2` | `prompt+pass` | `fail` | `fail` | `test_failure` | `true` |

Candidates are recreated deterministically from the pinned dataset: the oracle recipe
uses `prompt + canonical_solution`; the failure recipe uses `prompt + pass`.
Candidate source is not copied into this report; its SHA-256 identity is present
in each result.
