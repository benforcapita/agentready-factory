# Marketing copy mutation checks

Install the project and PDF dependencies as in the Test workflow, then run from
the repository root:

```sh
python tools/lint-mutation/run.py --seed 567 --budget-seconds 90 > lint-mutation.json
```

The seed selects Unicode spacing, punctuation and markup variants for four
probes derived from three checked-in drafts. Each probe keeps its place in the
draft. The same seed and fixtures produce the same JSON on a completed run.
The dedicated pull request job uploads this JSON and runs lint without network
access.

The tool exits nonzero for any `new_survivors`, `errors` (including failed
benign controls and timeouts), or `unrun` cases. A different lint finding
(including a warning) cannot satisfy a probe. HTML title probes require a
rendered finding, not just a source match.

## Known survivor on main

`offer-amount/mixed-language-title` replaces the amount line in seed
`01-price-in-offer` with `<span title="50 USD">More details</span>`. At main
`e5256b61c31418cd4cde22e9dc5be231b2f3ff43`, lint exits 0 with no hard findings.
The job runs and counts this exact case, but only that baseline outcome appears
in `known_survivors` instead of `new_survivors`. A future rendered hard finding
counts as caught; remove the exemption when fixing the rule.

This is bounded regression coverage for in-scope offer amounts, pricing phrases
and English/Hebrew role wording. It does not exhaust the rule set, combinations
of edits, or other currency tokens.
