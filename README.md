# agentready-factory

Command-line tools for the AgentReady report factory. `arf validate` reads a
request YAML file. `arf collect` collects passive HTTP evidence. `arf notion check-schema` checks
Notion schemas. `arf scaffold`, `arf render` and `arf lint` write, render and
check report drafts. `arf replay` replays a frozen evidence bundle offline.

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

## Scaffold, render and lint

```sh
arf scaffold fixtures/scaffold/request-he.yaml --out runs/example --run-id arf-example-com-20261120-1000-ab12 --prompts fixtures/prompts/recorded
arf render fixtures/golden/lehamim/report.md --out out/shadow --run-id ci-shadow
arf lint fixtures/golden/lehamim --final --ack fixtures/lint/lehamim/lint-ack.json --out out/lehamim-lint.json
```

These subcommands run the `python -m arf.scaffold`, `python -m arf.render` and
`python -m arf.lint` entry points with the same flags, output and exit codes.
Run `arf <command> --help` for the flags.

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

## Replay

```sh
arf replay fixtures/golden/lehamim --out replay/lehamim --prompts fixtures/prompts/recorded \
  --ack fixtures/lint/lehamim/lint-ack.json
```

Replay rebuilds a run from a frozen bundle and recorded Prompt Versions with every socket
connection and DNS lookup refused. It checks each bundle file against `SHA256SUMS.txt`, writes
`readers/`, `prompts-used.json`, `report.md`, `notes-for-ben.md`, `report.html`, `report.pdf` and
`lint.json` to the empty `--out` folder, then writes `replay.json` with the golden checks: section
order and table shapes match the bundle's `report.html`, lint passes and the PDF has 2–4 pages.
Shadow replays (the default) carry the draft marker; `--final` must reproduce `report.html`
byte for byte. It exits 1 when a check fails. CI runs it in a network namespace with no network.

## Notion

```sh
arf notion check-schema fixtures/notion/recorded
```

The [LLD §3](https://docs.google.com/document/d/1pzluCsi12dcVTe8pNHDndXz6k08B6rA_LNhlYXbi17Q/edit)
data model is defined in code in `src/arf/notion/schemas.py`: the `🏭 Factories`
hub page, the `AgentReady Report Factory` page under it, the Tasks, Runs and
Artifacts databases, and the Factory Config page with its properties table. Task
`Status` options come from LLD §4.1.

`check-schema` reads a directory of recorded Notion API responses from a test
copy, one `<key>.json` per object: `factories-hub`, `factory-page`,
`factory-config` (retrieve page), `tasks`, `runs`, `artifacts` (retrieve
database) and `factory-config-table` (the table's block children). It checks
titles and parent pages; that every property in code exists with the same name
and type; that select and status properties offer every listed option; and that
relations point to the right database. Extra properties are allowed. Each
problem names the object and property, and the command exits 1 when any check
fails. It makes no Notion calls, needs no token, and never creates or changes a
database.

Not checked: the Prompt Versions relation target (PRO-404's database), the
single-row limit on `Latest run`, Factory Config value types (§3.4 gives none),
and `Requested by` options beyond `ben` (§3.1 says "bot slugs + ben").

`fixtures/notion/recorded` passes. `fixtures/notion/renamed-property` is the same
copy with the Runs `Status` property renamed to `Run status` and fails.

## Orchestrator (T8a)

```sh
# from the repository root (fixture paths are relative; override with --golden/--ack)
python -m arf.orchestrator dry-run fixtures/requests/sample.yaml --out /tmp/arf-dry-run --duplicate
python -m arf.orchestrator dry-run fixtures/requests/sample.yaml --out /tmp/arf-blocked --force-lint-failure
```

`src/arf/orchestrator` holds the LLD §4 Task and Run state machines. Every Task
`Status` from §4.1 and every Run `Status` has an explicit transition table;
any other write raises `IllegalTransition`. The happy path is Requested →
Claimed → Collecting evidence → Drafting → Rendering → Checking → Needs review.

- **Idempotency key:** `domain|Israel date|trigger|Musy message_id` (`-` without
  a Musy lead; `www.` and case are folded). A repeat request of an open
  (queued/running) or finished Task reuses its latest Run and is recorded as a
  `Duplicate` Task; `force: true` starts a new Task and Run instead.
- **Fix loop:** a failed lint sends the Task back to Drafting with the failing
  findings. After 2 fix attempts the Run ends `guardrail_blocked` and the Task
  `Guardrail blocked`. A step exception ends the Run `failed` and the Task `Failed`.
- **Retries (LLD §4.1/§4.2):** a repeat of a `Failed` or `Guardrail blocked` Task
  starts no Run unless it has `force: true`; it is recorded as `Duplicate` and
  `Submission.needs_force` is set. With `force: true` (or `Orchestrator.retry`)
  the same Task returns to `Claimed` with a new Run whose `Retry of` is the
  previous Run. Finished Runs (`succeeded`, `guardrail_blocked`, `failed`,
  `cancelled`) reject every further write.

`dry-run` uses an in-memory store that logs each Tasks/Runs write with Notion
property names, and recorded steps: the Lehamim golden `report.md` with a draft
marker, `render_html`, and the real linter with the golden acks. It skips DNS
site validation and makes no network or Notion calls. Drive upload, Artifacts
rows, the Command Center row and the result comment are T8b (PRO-449).

## Publish (T8b)

```sh
python -m arf.publish dry-run fixtures/requests/sample.yaml --out /tmp/arf-publish
python -m arf.publish dry-run fixtures/requests/sample.yaml --out /tmp/arf-publish-eval --eval
```

`arf.publish` runs after a passing check, before the Run is marked `succeeded`:

- Writes `lint.json` and `manifest.json`, then uploads `report.md`, `report.html`,
  `report.pdf`, `notes-for-ben.md`, `evidence.zip`, `lint.json`, `prompts-used.json`
  and `manifest.json` (whichever exist) to `AgentReady reports/<date> <domain> <run id>/`.
- The Drive interface has three methods: `find_folder`, `create_folder` and `upload`.
  Files inherit the private folder's access; nothing changes who can open them.
  `tests/test_publish.py` greps `src/arf/publish` for sharing and permission calls.
- Records one Artifacts row per file (Kind, Audience, Drive link, Box path, SHA-256,
  size, draft marker) and links them on the Run.
- Creates a Command Center review row and sets the Task's `Command Center item`,
  except in `eval` mode.
- A publish error ends the Run `failed` and the Task `Failed`.
- `result_comment` returns the result comment text and ends with
  "Nothing sent; nobody contacted." The dry run writes it to `result-comment.md`;
  nothing is posted.

The dry run uses an in-memory Drive and Notion store and makes no network calls.
