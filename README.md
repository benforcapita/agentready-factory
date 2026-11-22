# agentready-factory

Command-line tools for the AgentReady report factory. `arf validate` reads a
request YAML file. `arf collect` collects passive HTTP evidence. `arf notion check-schema` checks
Notion schemas. The `scaffold`, `render`, `lint`, and `replay` commands are
placeholders.

## Setup

With Python 3.12 or later and an active virtual environment:

```sh
python -m pip install .
arf --help
arf validate fixtures/requests/sample.yaml
python -m unittest discover -v
```

Validation checks the request against the [LLD §2.1](https://docs.google.com/document/d/1pzluCsi12dcVTe8pNHDndXz6k08B6rA_LNhlYXbi17Q/edit)
schema and resolves the HTTPS site's hostname to check that all addresses are
public. It makes no HTTP requests. Tests mock DNS.

Some proxied runtimes answer every DNS lookup with a fake-IP address from
`198.18.0.0/15`. When every answer is in that range, validation warns that the
public address check was skipped and accepts the request. Literal private IPs
are still rejected in every form a URL parser accepts: shorthand, octal and hex
IPv4 (`127.1`, `0x7f.1`), non-ASCII forms that fold to them after IDNA mapping
(fullwidth digits, ideographic dots, zero-width and soft-hyphen characters),
and IPv6 addresses embedding a private IPv4 (`[::127.0.0.1]`, `[64:ff9b::7f00:1]`).
`localhost` and other special-use names (`.local`, `.internal`, `.home.arpa`,
`.test`, `.invalid`, `.onion`, `.alt`) and private answers mixed with fake-IP
answers are rejected too. A public DNS name that really points at a private
address cannot be detected behind a fake-IP resolver, so `collect` must repeat
the check on the real egress.

Validation defaults to the `shadow` phase. Pass the current Factory Config value
as `--phase shadow|reviewed|volume`. For `mode: eval`, set `eval_case` to the
HTTPS eval case URL and `fixture_bundle` to a local bundle path (relative paths
are interpreted relative to the request file).

`allow_public_scan` defaults to `false`. To request a public scan, include Ben's
timestamped approval for this run as text in `ben_ok`. Validation checks that the
text is present.

## Collect

```sh
arf collect fixtures/requests/sample.yaml --out evidence/sample --phase shadow
```

Collection validates first and requires an empty output directory. Passive GETs
probe nine identities across four paths on the apex and `www` hosts (72 rows),
preserving explicit ports. IP literals use only one host, never `www.<ip>`.
Probe redirects are not followed; browser-only discovery follows at most three
HTTP(S) redirect hops, checking each target.

One **global budget of 100 requests per run** covers probes, discovery and every
redirect hop. Exhaustion sends no more requests and writes `skipped_budget`
rows for remaining probes/discovery; budget exhaustion alone exits 0.
Environment configuration (explicit Python kwargs take precedence):

- `ARF_MAX_REQUESTS`: non-negative global budget (default `100`).
- `ARF_FAKE_IP_POLICY`: `refuse` (default) or `doh_verify`.
- `ARF_DOH_ENDPOINT`: HTTPS DNS JSON endpoint (default
  `https://cloudflare-dns.com/dns-query`).

`doh_verify` rechecks all-fake system DNS answers through HTTPS A and AAAA
queries, requiring every returned address to be public. Empty, malformed,
failed or timed-out verification and mixed fake/real system answers are refused.
After verification, connections pin the system fake address for proxy egress;
DoH traffic does not consume the site budget. Requests retain the 10-second
wall-clock deadline, capped bodies and HTTPS hostname/certificate checks.

Malformed discovery Locations are recorded as errors; unsafe/invalid redirect
targets are recorded as refused. Both continue to later discovery paths/hosts.
An unsafe probe host can still hard-stop. Completed bundles with errors exit 1.
Outputs include `probes.csv`, discovery rows/bodies, `errors.json`, and
`evidence.json` with counts, policy/DoH answers, timestamps and file hashes.

**Limitations:** the proxy may resolve differently from DoH (accepted for passive
GETs). `collect` does not enforce the request's `allow_public_scan` flag; that
policy remains pending CTO/Engineering Lead decision.

See [collect details](src/arf/collect/README.md) for configuration, guard rules
and offline sample generation; the [sample bundle](fixtures/collect/sample/)
uses only a local test server.

## Notion

```sh
arf notion check-schema fixtures/notion/recorded
```

The Factories hub page, the Tasks, Runs and Artifacts databases and the Factory
Config page are defined in code in `src/arf/notion/schemas.py` (LLD §3). The
schema there is provisional until LLD §3 is copied into Linear (PRO-510).

`check-schema` reads a directory of recorded Notion API responses, one
`<key>.json` per object (`factories-hub`, `tasks`, `runs`, `artifacts`,
`factory-config`): the body of a retrieve-database or retrieve-page call against
a test copy. Every property in code must exist with the same name and type, and
database select properties must offer every listed option. Extra properties are
allowed. Each problem names the object and property; the command exits 1 when any
check fails. It makes no Notion calls, needs no token, and never creates or
changes a database.

`fixtures/notion/recorded` passes. `fixtures/notion/renamed-property` is the same
copy with the Runs `State` property renamed to `Run state` and fails.
