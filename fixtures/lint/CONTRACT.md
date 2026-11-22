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
| missing-draft-marker | hard | Draft-only / §6.2: run comment in md/html, bilingual HTML footer and verifiable PDF marker; shadow/reviewed unless --final |
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
| uncited-finding | hard | Evidence grounding: explicit report citations must exist; structured evidence paths must exist and match any supplied manifest |
| blocked-single-source | hard | Evidence grounding: ≥2 independent source aliases unless explicitly uncertain |
| secret | hard | Secrets: credential prefixes, assignments, private keys, bearer tokens, ≥80-character base64 runs |
| pdf-length | warning | Report longer than four PDF pages |
| public-side-effect | warning, deferred | Recorded public side effect: no producer/record contract exists yet |

`placeholder-left` was removed: it is not an LLD rule and required a golden ack.

Matching uses NFKC, removes all Unicode Cf format characters and removes Hebrew
Mn niqqud/cantillation in U+0591–U+05C7. Other combining marks are preserved.
Semantic excerpts use this matching text, so invisible/fullwidth variants have
stable fingerprints equivalent to their plain forms. Input hashes still cover
the original bytes, and line numbers are preserved. Pricing phrases allow
whitespace and English plurals (fees, discounts, retainers, hourly rates).

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

Neither the repository nor the golden bundle defines a findings schema. The
minimal supported findings.json contract is
`{"findings": [{"id": "finding-1", "evidence": ["probes.csv"]}]}`.
IDs must be unique nonempty strings or integers. Every finding needs a list of
string paths. An empty evidence list, a missing/outside-root file, or a path not
listed in a supplied evidence.json is a hard `uncited-finding` failure. Without
evidence.json, file existence and containment are still enforced; manifest
membership cannot be checked. Unsupported shapes exit 2, never a passing skip.
An empty findings list is valid. Referenced files are included in input hashes.
Prose-to-evidence semantic verification remains outside this deterministic
contract. Public-side-effect checks are deferred: the request
model has allow_public_scan/ben_ok authorization fields, but no run event record
format exists. Authorization alone is not evidence of a public side effect.

An existing report.pdf in shadow/reviewed (unless --final) must contain the exact
`טיוטה — לא לשליחה · DRAFT` marker in raw bytes or a zlib-decompressed FlateDecode
stream. UTF-8, UTF-16BE and UTF-16LE are checked, with an 8 MiB decoded-stream
limit. An absent/unreadable marker is a hard `missing-draft-marker` with a
"could not be verified" message, not a skip. Volume remains marker-exempt.
Full PDF text extraction, including CID fonts, would require a new dependency;
none is added. Encoded glyph IDs, other filters and unreadable streams fail
closed rather than being assumed safe.

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
