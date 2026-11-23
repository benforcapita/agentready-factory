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

Pricing and role phrases use indexed visible-text skeletons: HTML tags/comments
and Markdown link destinations are excluded; entities are decoded. NFKC and
casefold precede a pinned stdlib-only UTS #39 letter-confusables table, including
Cyrillic/Greek, Hebrew/Latin lookalikes and small capitals. Compatibility letters
(including superscripts) and decomposed accents fold to their base letters.
Only Unicode L*/N* survive: every Cf, Mn/Mc/Me, Pd, whitespace and punctuation
separator disappears, including U+2053. English plurals and Hebrew inflections
are explicit phrase variants; separators inside words are accepted.
Non-final Hebrew construct words also accept their plural (ת→ות, e.g.
הצעות מחיר), and a leading definite ה on later words is optional (עלות שירות).
Skeleton spans map back to matching-source positions. Both endpoints must be
visible original word boundaries, so `paper hour` cannot match `per hour`, nor
can a match split a compatibility ligature. Excerpts retain the matched source
span. Existing artifact preprocessing still folds Cf/marks/dashes for stable
legacy fingerprints; hashes always cover original bytes. Files are not changed.
Money uses the same folding/index map while retaining currency symbols; Unicode
decimal digits, punctuation-separated amounts, adjacent ISO codes and the
EUR/GBP/JPY/CHF/AUD/CAD/SGD/AED/HKD/NZD/BRL and HK$/NZ$/A$/C$/R$ forms are covered.
Bounded limits: the confusable table is finite, not visual OCR; arbitrary homoglyph
sequences, phonetic transliteration and full Markdown layout are not inferred.
Unfinished HTML tokens are treated as literal text, failing closed for separator
insertion probes rather than allowing a stray `<` to swallow a prohibited phrase.
Other non-phrase rules retain their existing syntax-aware regexes and normalization.

Hebrew phrase matching preserves Hebrew letters while folding non-Hebrew
confusables; final ן remains distinct at word edges, so it cannot be a false ו
prefix. It accepts stacked ו/ה/ב/כ/ל/מ/ש
clitics on the first word and one productive inflection on a word; nonfinal
words only take ים/ות/י/ת, while possessive/construct suffix forms are supported
on the final word. The generated matrix crosses every prefix stack through length
three with each phrase and with suffixes on representative meaningful stems;
longer stacks and B1 forms are also checked. A leading ה is not accepted before
an already-yod-final first stem such as ראשי, avoiding the B2 false positive in
בדף הראשי צוות האתר. English word-boundary and separator behavior is unchanged.

Markdown link destinations are excluded, but footnote text and reference labels
remain in the source skeleton and are linted; numeric footnote markers remain
syntax. This closes the small B3 source-text case. Hidden/display:none HTML
subtrees and numeric superscript/subscript annotations keep their source-masking
behavior in this pre-check; render-then-lint below covers what T5 delivers.
Previously supported leader/feminine/
development role variants, quoted/discounted/per-hr pricing, and currency forms
remain unchanged; this fix adds no further catch classes. Semantic context is not
inferred.

REWORK #5 scoped observations: N1 sentence punctuation remains fail-closed
because REWORK #4 explicitly requires punctuation insertion and price: list to
hard-fail; lowercase ISO currency homonyms remain currency candidates. N2 bidi
override visual reversal is not reconstructed (requires rendered bidi semantics).
N4 quote/team lead context remains phrase-based per the LLD, not semantic judging.
N5 confusables are a frozen curated derivative of security/latest (458 source
agreements and 48 author folds), not a verbatim subset or a live network input.

## Render-then-lint (PRO-561)

The Markdown source lint above is a fast pre-check. `arf lint` then renders
`report.md` with T5 (`render_html`, then the same WeasyPrint layout as
`render_pdf`) and runs `price-in-offer`, `currency-outside-offer` and `ben-role`
— the rules whose source matcher masks markup — on two customer-visible views,
using the same phrase lists, Hebrew matching, normalization and proposal-section
logic as the source lint. No rule definition changes.

- `rendered:report.pdf`: the text WeasyPrint lays out on the PDF pages, after CSS
  (class selectors, generated content, `visibility`). This is what B3 footnote
  and reference-label text becomes once rendered.
- `rendered:report.html`: every text node, HTML comment and reader-visible
  attribute value in the delivered HTML body, including `hidden`, `display:none`
  and `visibility:hidden` subtrees; `<script>`, `<style>` and the document's real
  `<head>` (before `<body>`) are not text. A `<head>` tag inside the body is
  ignored, as browsers and WeasyPrint do, so its content and everything after it
  is still linted. Each hidden subtree and comment is linted as its own unit: it
  is bounded by line breaks, so hidden text never runs into the adjacent visible
  word. Reader-visible attributes are `title`, `alt`, `aria-label`,
  `aria-description`, `aria-roledescription`, `aria-placeholder`,
  `aria-valuetext`, `placeholder`, `value` on `input`/`button`/`option` and
  `label` on `option`/`optgroup`/`track`. Their text is linted at the end of the
  enclosing block, so the same section logic applies.

Hidden-HTML rule (N2): hiding is not an exemption. `report.html` is itself a
customer deliverable, and hidden text and comments stay readable through view
source, copy, search, reader modes and AI agents. A rule match inside hidden HTML
is the same finding as in visible text. The same holds for attribute text the
reader can see (hover tooltip, image fallback, form text) or that assistive tech,
view source and AI agents read: it is customer-visible and linted like hidden
text. Hidden or attribute text that matches no rule passes.

A rendered finding is reported (file `rendered:report.pdf` or
`rendered:report.html`, line in that view) only when `report.md` or `report.html`
has no finding with the same rule and matched text. A render failure is a lint
error (exit 2). `--source-only` runs just the pre-check and records a
`render-then-lint` skipped check; it must not be used for a draft that leaves the
factory. Fixtures: `fixtures/lint/render/cases.json`.

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
`טיוטה — לא לשליחה · DRAFT` marker as an extracted text line on every page.
The stdlib extractor follows the catalog's /Pages → /Kids → /Page tree, inherits
page resources, and reads only each page's referenced /Contents streams (plain
or FlateDecode). It extracts Tj/TJ/single-quote/double-quote text through the
selected font's /ToUnicode CMap (bfchar and bfrange), including CID glyph IDs.
It handles ordinary indirect objects and Flate object streams, as emitted by
the actual T5 renderer. Both logical Hebrew and the renderer's visual ordering
are recognized; no external extraction executable or dependency is used.
Metadata/Info/XMP, unreferenced streams and raw byte matches never count.
Input/decoded streams and total extracted text are bounded to 8 MiB. Unsupported
filters, encryption, incremental updates, missing CMaps, unsupported text/graphics
states, malformed objects and extraction errors fail closed with hard
`missing-draft-marker` and the message `PDF marker unverifiable`, not a
skip. This is conservative page-text extraction, not a general PDF renderer or
verification of footer geometry. Volume and --final remain marker-exempt.

Missing report.pdf skips PDF length. An existing PDF is counted using
`/Type /Page` objects; zero visible objects is an explicit skip (compressed object
streams are not decoded for length). PDF secret scanning and Notion pre-write secret
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
