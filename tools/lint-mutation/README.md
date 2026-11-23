# Marketing copy mutation checks

Install the project and PDF dependencies as in the Test workflow, then run from
the repository root:

```sh
python tools/lint-mutation/run.py --seed 567 --budget-seconds 150 > lint-mutation.json
```

The seed selects Unicode spacing, punctuation and markup variants for four
copy probes derived from three checked-in drafts, plus URL variants for a
tracking-parameter probe and HTML/Markdown media variants for a tracking-pixel
probe (six probes, five drafts, 36 cases). Each probe keeps its place in the
draft. The same seed and fixtures produce the same JSON on a completed run,
except `elapsed_seconds`, the wall-clock time of the run.

The dedicated pull request job checks out the pull request merge ref (the
default `actions/checkout` ref for `pull_request`), so it tests the head as it
would land on the current base; the log prints the merge, head and base SHAs.
It runs lint without network access and always uploads the JSON. If the tool
crashes inside Python, it still prints a report with `crash` set and `crash`
in `errors`, and exits 2. If it exits without valid JSON, the job writes a
stub report with `crash` and `exit_code`, uploads it, and fails.

The tool exits nonzero for any `new_survivors`, `errors` (including failed
benign controls, timeouts and crashes), or `unrun` cases. A different lint finding
(including a warning) cannot satisfy a probe. HTML title probes require a
rendered finding, not just a source match.

## Known survivor on main

`offer-amount/mixed-language-title` replaces the amount line in seed
`01-price-in-offer` with `<span title="50 USD">More details</span>`. At main
`e5256b61c31418cd4cde22e9dc5be231b2f3ff43`, lint exits 0 with no hard findings.
The job runs and counts this exact case, but only that baseline outcome appears
in `known_survivors` instead of `new_survivors`. A future rendered hard finding
counts as caught; remove the exemption when fixing the rule.

This is bounded regression coverage for in-scope offer amounts, pricing phrases,
English/Hebrew role wording, tracking URL parameters and tracking images. It
does not close the tooltip gap above, and it does not exhaust the rule set, combinations
of edits, or other currency tokens.
