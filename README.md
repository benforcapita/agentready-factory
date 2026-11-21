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

Validation defaults to the `shadow` phase. Pass the current Factory Config value
as `--phase shadow|reviewed|volume`. For `mode: eval`, set `eval_case` to the
HTTPS eval case URL and `fixture_bundle` to a local bundle path (relative paths
are interpreted relative to the request file).

`allow_public_scan` defaults to `false`. To request a public scan, include Ben's
timestamped approval for this run as text in `ben_ok`. Validation checks that the
text is present.
