# agentready-factory

Command-line tools for the AgentReady report factory. `arf validate` reads a
request YAML file. `arf collect` collects passive HTTP evidence. The `scaffold`,
`render`, `lint`, `notion`, and `replay` commands are placeholders.

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

Collection validates the request first, prints validation warnings, and refuses
a non-empty output directory. It probes the site's apex and `www` host (apex
first), independently of the URL's path, preserving any explicit site port.
For each host, it GETs `/`,
`/robots.txt`, `/llms.txt`, and `/sitemap.xml`, in that order, with nine identities:
browser, Googlebot, GPTBot, ChatGPT-User, OAI-SearchBot, ClaudeBot, Claude-User,
PerplexityBot, and Google-Extended. Google-Extended is a policy identity rather
than a separate official HTTP crawler; its synthetic UA makes this probe
explicit. Probe redirects are recorded as raw responses, never followed.

Five additional browser-only discovery paths are fetched once per host:
`/.well-known/ai-catalog.json`, `/llms-full.txt`, `/.well-known/security.txt`,
`/sitemap_index.xml`, and `/.well-known/ai-plugin.json`. Only these discovery
requests follow HTTP(S) redirects, up to three hops. Each hop consumes a request
against its actual target host. **The 60-request cap is per host**, reconciling
the required 72 probe rows (9 × 4 × 2) with the ticket's limit. Normally there
are 41 requests per host; even three redirects on all five discovery paths fit
at 56. A hard counter stops before any request exceeding the cap. Requests
use a 10-second wall-clock deadline covering DNS, connect, headers and body,
and read at most 2 MiB per response; `size` is the body
bytes received, not Content-Length or the decompressed size. No cookies,
authentication, forms, active scans, or non-GET requests are used.

Every target, including redirects, is classified with the validator's IDNA,
numeric IPv4, embedded IPv4/IPv6 and special-use host rules. At connection time
all DNS answers must be public. Fake-IP answers (`198.18.0.0/15`) are refused
with a real-egress verification error, even though validation only warns.
Connections pin a vetted IP without a second lookup; HTTPS uses certificate
verification and SNI for the original hostname and HTTP uses its Host header.
A guard refusal or exhausted budget immediately aborts the run (a partial
bundle may remain). Network/HTTP errors get `error` probe rows and details in
`errors.json`; a completed bundle with such errors still exits 1. Success exits
0. Discovery redirect exhaustion also exits 1.

Outputs:

- `probes.csv`: exactly `domain,path,agent,status,server,cf_ray,size`, ordered
  host → path → identity; absent headers are empty.
- `discovery.csv` and `discovery/*.body`: each fetched discovery response,
  including redirect bodies, with its URL, status and size.
- `errors.json`: short network/HTTP error details, empty on success.
- `evidence.json`: tool/version, site/hosts, Asia/Jerusalem ISO timestamps,
  total/per-host request counts, config summary, and sorted relative paths,
  byte counts and SHA-256 for every other bundle file.

The committed [sample bundle](fixtures/collect/sample/) is generated entirely
offline against a local threading HTTP server (synthetic hostnames, explicit
host-scoped loopback allowance). To regenerate into an empty directory:

```sh
python -m tests.collect_server fixtures/collect/sample-new
```

Only the internal Python API accepts a resolver, scheme, port, and host-scoped
guard-policy allowance for offline tests. None is exposed in the CLI; its
default policy is always public-only. Tests never resolve or fetch real sites.
