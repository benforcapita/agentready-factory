# agentready-factory

Command-line tools for the AgentReady report factory. `arf validate` reads a
request YAML file. The `collect`, `scaffold`, `render`, `lint`, `notion`, and
`replay` commands are placeholders.

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
