# LLD §6.4 lint contract

Run `python fixtures/lint/regenerate.py` to regenerate all twelve seeded drafts,
their acknowledgement copies and the thirteen expected outputs. Golden inputs
are never written. The golden case uses `--final`, not a marker exemption for
reviewed mode. Seeded drafts carry `<!-- arf:draft run=lint-fixture -->`.

## Rule map

| Rule | Level | LLD §6.4 bullet |
|---|---|---|
| price-in-offer | hard | No price: currency amounts in proposal/§4–5/private notes; pricing phrases everywhere |
| generic-price | warning | No price: generic מחיר / price / cost |
| currency-outside-offer | warning | Currency amounts outside the proposal |
| code-block | hard | No detailed fixes: customer report code blocks/inline code |
| implementation-detail | hard | No detailed fixes: banned implementation tokens |
| extra-offer-steps | hard | No detailed fixes: maximum four §4 pillars |
| detailed-offer-step | hard | No detailed fixes: ≤40 words per pillar, no step-by-step instructions |
| ben-role | hard | Role wording: complete banned phrase list |
| employer-mention | hard | Role wording: configured employer |
| prepared-by | hard | Role wording: exact configured prepared-by text |
| missing-draft-marker | hard | Draft-only / §6.2: run comment in md/html and bilingual HTML footer; shadow/reviewed unless --final |
| sent-status | hard | Draft-only: no sent/נשלח status lines |
| recipient-email | hard | Draft-only: only configured Ben email on his contact line |
| tracking-pixel | hard | No tracking: scripts, remote images, pixels, tracker media |
| tracking-param | hard | No tracking: utm_ and click identifiers |
| link-shortener | hard | No tracking: shortener hosts |
| non-evidence-url | hard | No tracking: URLs outside evidence hosts |
| required-headings | hard | Structure: summary, numbered §1–5, appendix in order |
| findings-table | hard | Structure: ≥3 data rows, exactly three columns |
| too-many-findings | warning | More than five findings (warning takes precedence over the 3–5 wording) |
| missing-appendix | hard | Structure: appendix table with ≥3 data rows |
| appendix-evidence | hard | Structure: each appendix row references a manifest file path |
| israel-time | hard | Structure: time blocks labeled Israel time / שעון ישראל |
| uncited-finding | hard | Evidence grounding: explicit report citations must exist; structured grounding deferred below |
| blocked-single-source | hard | Evidence grounding: ≥2 independent source aliases unless explicitly uncertain |
| secret | hard | Secrets: credential prefixes, assignments, private keys, bearer tokens, ≥80-character base64 runs |
| pdf-length | warning | Report longer than four PDF pages |
| public-side-effect | warning, deferred | Recorded public side effect: no producer/record contract exists yet |

`placeholder-left` was removed: it is not an LLD rule and required a golden ack.

## Input contracts and skips

The existing collector writes `evidence.json` with `site`, `hosts` and
`files: [{path, bytes, sha256}]`. Lint uses that manifest, hashes available referenced
files, and derives evidence hosts from the manifest and collector discovery.csv
URL column (not arbitrary links inside captured pages). Paths must stay
inside the draft directory. Appendix rows map by explicit manifest paths. It does
not infer prose-to-evidence matches or evidence independence with a model.

No Factory Config exists in the base repository. `--factory-config PATH` accepts
JSON or a flat YAML mapping with three nonempty inline string scalars:
`prepared_by`, `contact_email`, `employer`. Complex YAML is rejected, not guessed;
the implementation uses only the standard library. Without config, prepared-by,
employer and recipient-email checks are explicitly skipped.

The golden bundle contains raw captures, probes.csv, reports and private notes,
but no evidence.json or findings.json. Missing evidence.json skips appendix
mapping and third-party host classification. Explicit local links in finding
rows are still checked for missing/path-escaping evidence files.

Structured findings grounding is deferred even if findings.json is supplied:
neither the repository nor the golden bundle defines its schema. The file is
parsed, hashed and scanned for secrets; the grounding skip explains this rather
than inventing fields. Public-side-effect checks are also deferred: the request
model has allow_public_scan/ben_ok authorization fields, but no run event record
format exists. Authorization alone is not evidence of a public side effect.

Missing report.pdf skips PDF length. An existing PDF is counted using
`/Type /Page` objects; zero visible objects is an explicit skip (compressed object
streams are not decoded). Binary PDF text extraction and Notion pre-write secret
scanning are deferred; raw evidence captures are inputs, not authored artifacts.
Secrets are scanned in report.md, report.html, notes-for-ben.md, findings.json and
evidence.json. Every deferred/missing-input check appears in `skipped_checks`.

## Golden-grounded narrowings

Generic price words, product-price descriptions and the customer query
`מגש אירוח עד 200 ₪` are warnings, not service-pricing fails. `בלי מחיר` in notes
is a warning. Missing source aliases in prose rows no longer imply an uncited
finding: LLD grounds structured findings against files, not an alias-word count.
Only blocking claims use the source alias map, with explicit uncertainty allowed.

Time checks honor a paragraph's label, a section heading's label, and an explicit
document-level “כל הבדיקות: … שעון ישראל” declaration in private notes. HTML
formatting newlines do not break metadata blocks at inline spans/line breaks.
These avoid the three false time-label failures initially found on the golden.

## T5 interoperability inspection

PR #8 head inspected: `1ee12f8f308b34965c3ee05b5df5620db96b3346`.
Its CLI reads Markdown without writing it and generates no `arf:draft` run
comment. The renderer would preserve an existing Markdown HTML comment, but
does not create one or accept a run ID. HTML emits the bilingual marker in a
top `.draft-marker` div and an `@page @top-center` print rule, not a footer.
Renderer changes are outside this ticket's allow-list.
