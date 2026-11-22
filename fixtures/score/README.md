# Score fixtures

- `request-public-scan.yaml`: `allow_public_scan: true` with a fixture `ben_ok` (gate allows; tests stub the runner).
- `request-public-scan-no-ben-ok.yaml`: `allow_public_scan: true` without `ben_ok` (gate must refuse).

Recorded is-agentic responses come from `fixtures/golden/lehamim/isag_api.json` (404) and
`isag_cli.json` (CLI `--json` output, also the API's 200 shape). Tests make no live calls.
