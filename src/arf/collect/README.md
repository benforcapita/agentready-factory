# Passive collection: budget and proxy policy

`arf collect REQUEST.yaml --out DIR [--phase ...]` validates first and then
performs passive GETs. No forms, credentials, cookies, or active scans are used.
The output directory must be empty. Probe redirects are not followed; discovery
follows at most three HTTP(S) redirects, guarding each target independently.

## Global budget

One **100-request budget per run** replaces the previous 60-per-host cap. Both
hosts, probes, discovery and each discovery redirect hop share this budget.
DoH verification traffic is separate and does not consume the site budget.
The counter records site GET attempts after the connection passes its guard;
failed HTTP attempts count, but guard refusals and failed connects do not.

Once exhausted, no more site requests are sent. Remaining probe rows and
discovery entries use `skipped_budget`; budget exhaustion alone is successful
(exit 0). A hostname still has 72 ordered probe rows (9 identities × 4 paths ×
apex/www); an IP literal has just one host and 36 rows, never `www.<ip>`.
Explicit site ports are preserved. Each request has a 10-second wall-clock
deadline covering DNS, verification, connect, headers and capped body reads.

## Environment and Python configuration

The existing CLI uses these environment variables through `collect()`:

| Environment | Python kwarg | Default |
| --- | --- | --- |
| `ARF_MAX_REQUESTS` | `max_requests` | `100` |
| `ARF_FAKE_IP_POLICY` | `fake_ip_policy` | `refuse` |
| `ARF_DOH_ENDPOINT` | `doh_endpoint` | `https://cloudflare-dns.com/dns-query` |

The Python API reads the environment only when the corresponding kwarg is
`None`; explicit kwargs win. Budgets must be non-negative integers; zero writes
only skipped entries. Invalid configuration exits 1. No new CLI flags or
request-schema fields are required. Tests additionally inject DNS/DoH resolvers,
scheme/port and host-scoped loopback allowances; the CLI exposes none of these.

## Fake-IP policy

`refuse` rejects fake-IP DNS answers (`198.18.0.0/15`) by default.
`doh_verify` permits an all-fake system DNS answer set only after querying both
A and AAAA through the configured HTTPS DNS JSON endpoint, with certificate
verification and the site's request deadline. Every real A/AAAA address must
pass the same public-address rules, without test-network allowances. Empty,
malformed, truncated, unsuccessful or timed-out verification fails closed.
Mixed fake/real system DNS answers always fail closed. Literal classification
and private/loopback/link-local/embedded-IP checks remain enforced.

After successful verification, collection pins the **system fake address**, not
the DoH address, to preserve the proxy egress path. Verification repeats at each
connection, including redirect targets. HTTPS verifies the original hostname
and uses it for SNI/Host. DoH uses GET queries with `name` and `type=A|AAAA` and
`Accept: application/dns-json`; endpoint redirects are not followed.

## Evidence and errors

`evidence.json` records `requests_made`, `max_requests`, per-host counters,
`fake_ip_policy`, and per-host `doh_answers` checked, alongside configuration,
Asia/Jerusalem timestamps, and sorted file sizes/SHA-256. Discovery rows with no
fetch use `skipped_budget`, `error` (malformed Location), or `refused` (unsafe
target); errors also appear in `errors.json`. These discovery failures do not
stop later paths/hosts. Completed bundles containing errors exit 1; an unsafe
probe host may still abort immediately and leave a partial bundle.

## Limitations

- The proxy may resolve differently from DoH. This uncertainty is accepted for
  passive GETs; DoH is not proof of the proxy's real destination.
- `collect` does **not** check the request's `allow_public_scan` flag (QA N4,
  pending CTO/Engineering Lead policy).
- The root README's earlier per-host-cap description is superseded by this
  module documentation for the global-budget/DoH implementation.

The sample at `fixtures/collect/sample/` is generated offline using
`python -m tests.collect_server fixtures/collect/sample` into an empty directory.
