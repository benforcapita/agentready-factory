import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zlib

from arf.lint import inspect_artifact, lint, load_config
from arf.lint.markers import DRAFT_MARKER
from arf.lint.rules import MESSAGES, normalize_for_matching

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures/lint"
GOLDEN = ROOT / "fixtures/golden/lehamim"
COMMENT = "<!-- arf:draft run=unit-test -->"
CONFIG = dict(prepared_by="Ben — Independent advisor", contact_email="ben@example.test", employer="Example Employer")
GOOD = COMMENT + """
# AgentReady report

Prepared by: Ben — Independent advisor

## Executive summary

Proposal: Let's talk.

## 1. Why it matters

Context.

## 2. Findings

| # | Finding | Impact |
|---|---|---|
| 1 | HTTP 403 and is-agentic blocked | Cannot read |
| 2 | Jina observation | Readability |
| 3 | sitemap observation | Discovery |

## 3. What works

Content.

## 4. Offer

1. Access policy.
2. Business identity.
3. Product representation.
4. Measurement.

## 5. Next

Talk.

## Appendix

| Source | Result |
|---|---|
| probes.csv | HTTP 403 |
| discovery/0-0-0.body | Read |
| discovery/0-0-1.body | Read |
"""

HTML_GOOD = COMMENT + """
<h1>AgentReady report</h1>
<h2>Executive summary</h2><p>Proposal: Let's talk.</p>
<h2>1. Why it matters</h2><p>Context.</p>
<h2>2. Findings</h2>
<table><thead><tr><th>#</th><th>Finding</th><th>Impact</th></tr></thead><tbody>
<tr><td>1</td><td>HTTP 403 and is-agentic blocked</td><td>Cannot read</td></tr>
<tr><td>2</td><td>Jina observation</td><td>Readability</td></tr>
<tr><td>3</td><td>sitemap observation</td><td>Discovery</td></tr>
</tbody></table>
<h2>3. What works</h2><p>Content.</p>
<h2>4. Offer</h2><ol><li>Access policy.</li><li>Business identity.</li>
<li>Product representation.</li><li>Measurement.</li></ol>
<h2>5. Next</h2><p>Talk.</p>
<h2>Appendix</h2>
<table><thead><tr><th>Source</th><th>Result</th></tr></thead><tbody>
<tr><td>probes.csv</td><td>HTTP 403</td></tr>
<tr><td>discovery/0-0-0.body</td><td>Read</td></tr>
<tr><td>discovery/0-0-1.body</td><td>Read</td></tr>
</tbody></table>
"""


def pdf_stream(content):
    compressed = zlib.compress(content)
    return (b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n2 0 obj\n"
            + f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode()
            + compressed + b"\nendstream\nendobj\n%%EOF")


def rules(content, file="report.md", **kwargs):
    return {f["rule"] for f in inspect_artifact(file, content, "shadow", **kwargs)}


class LintTests(unittest.TestCase):
    def cli(self, directory, *args):
        return subprocess.run([sys.executable, "-m", "arf.lint", str(directory), *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True)

    def write(self, root, file, content):
        path = root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_text_rules_positive_and_negative(self):
        cases = {
            "price-in-offer": GOOD.replace("Talk.", "50 USD"),
            "generic-price": GOOD.replace("Content.", "Product price and cost"),
            "currency-outside-offer": GOOD.replace("Content.", "A customer asks for a £20 product"),
            "code-block": GOOD + "\n`example`\n",
            "implementation-detail": GOOD + "\nopeningHoursSpecification\n",
            "ben-role": GOOD + "\nBen is a team lead",
            "employer-mention": GOOD + "\nExample Employer",
            "prepared-by": GOOD.replace("Ben — Independent advisor", "Somebody else"),
            "recipient-email": GOOD + "\nTo: recipient@example.test",
            "sent-status": GOOD + "\nStatus: sent",
            "tracking-pixel": GOOD + '<img style="width:0px;height:1px" src="local.gif">',
            "tracking-param": GOOD + "https://example.test/?gclid=abc",
            "link-shortener": GOOD + "https://bit.ly/example",
            "non-evidence-url": GOOD + "https://unrelated.test/page",
            "missing-appendix": GOOD.replace("| probes.csv | HTTP 403 |\n", ""),
            "required-headings": GOOD.replace("## 1. Why it matters", "## Why it matters"),
            "findings-table": GOOD.replace("| 2 | Jina observation | Readability |\n", ""),
            "too-many-findings": GOOD.replace("| 3 | sitemap observation | Discovery |", "| 3 | sitemap observation | Discovery |\n| 4 | Jina | OK |\n| 5 | Jina | OK |\n| 6 | Jina | OK |"),
            "israel-time": GOOD.replace("Content.", "Checked at 09:47"),
            "missing-draft-marker": GOOD.replace(COMMENT, ""),
            "secret": GOOD + "\npassword = obviously_fake_value_123456789",
            "blocked-single-source": GOOD.replace(" and is-agentic", ""),
            "extra-offer-steps": GOOD.replace("4. Measurement.", "4. Measurement.\n5. Another pillar."),
            "detailed-offer-step": GOOD.replace("1. Access policy.", "1. " + "word " * 41),
        }
        for rule, content in cases.items():
            with self.subTest(rule=rule):
                options = dict(config=CONFIG, allowed_hosts={"example.test"})
                self.assertIn(rule, rules(content, **options))
                self.assertNotIn(rule, rules(GOOD, **options))
        self.assertEqual(rules(GOOD, config=CONFIG), set())
        self.assertNotIn("placeholder-left", MESSAGES)
        self.assertNotIn("placeholder-left", rules(GOOD + "[CONTACT_NAME]"))

    def test_lehamim_and_expected(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "lint.json"
            process = self.cli(GOLDEN, "--final", "--ack", FIXTURES / "lehamim/lint-ack.json", "--out", out)
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            self.assertEqual(out.read_bytes(), (FIXTURES / "expected/lehamim.json").read_bytes())
            self.assertEqual(json.loads(out.read_text())["summary"]["hard_fail"], 0)
            first = out.read_bytes()
            self.assertEqual(self.cli(GOLDEN, "--final", "--ack", FIXTURES / "lehamim/lint-ack.json", "--out", out).returncode, 0)
            self.assertEqual(out.read_bytes(), first)

    def test_seeded_cli_and_expected(self):
        directories = sorted((FIXTURES / "seeded").iterdir())
        self.assertEqual(len(directories), 12)
        with tempfile.TemporaryDirectory() as temp:
            for directory in directories:
                with self.subTest(seed=directory.name):
                    out = Path(temp) / "lint.json"
                    process = self.cli(directory, "--out", out)
                    self.assertEqual(process.returncode, 1, process.stderr)
                    result = json.loads(out.read_text())
                    active = {f["rule"] for f in result["findings"] if not f["acknowledged"]}
                    self.assertEqual(active, {directory.name[3:]})
                    self.assertEqual(out.read_bytes(), (FIXTURES / "expected" / (directory.name + ".json")).read_bytes())
                    first = out.read_bytes()
                    self.assertEqual(self.cli(directory, "--out", out).returncode, 1)
                    self.assertEqual(out.read_bytes(), first)

    def test_acks_and_line_stability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            content = GOOD + "\nProduct price\n\nBen is a team lead"
            self.write(root, "report.md", content)
            findings = lint(root)["findings"]
            acks = [{**{key: f[key] for key in ("rule", "file", "fingerprint")}, "reason": "Test acknowledgement"} for f in findings]
            acks.append(dict(rule="generic-price", file="report.md", fingerprint="unused", reason="Old finding"))
            self.write(root, "lint-ack.json", json.dumps(dict(version=1, acks=acks)))
            self.write(root, "report.md", "\n\n" + content)
            result = lint(root)
            self.assertEqual(result["summary"], dict(hard_fail=1, warning=1, acknowledged=1, unacknowledged_warning=0))
            self.assertEqual({a["status"] for a in result["unused_acks"]}, {"unused", "hard-fail-ack-ignored"})
            self.write(root, "report.md", GOOD + "\nProduct price")
            self.assertEqual(self.cli(root).returncode, 0)
            self.assertTrue((root / "lint.json").exists())

    def test_input_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(self.cli(root).returncode, 2)
            self.write(root, "report.md", GOOD)
            for content in ("{", "[]", '{"version":2,"acks":[]}', '{"version":1,"acks":[{}]}'):
                self.write(root, "lint-ack.json", content)
                self.assertEqual(self.cli(root).returncode, 2)
            (root / "lint-ack.json").unlink()
            for args in (("--phase", "unknown"), ("--ack", root / "missing.json"),
                         ("--factory-config", root / "missing.json"), ("--out", root / "report.md"),
                         ("--out", root / "missing/lint.json")):
                self.assertEqual(self.cli(root, *args).returncode, 2)
            self.write(root, "evidence.json", '{"files": ["wrong"]}')
            self.assertEqual(self.cli(root).returncode, 2)
            (root / "evidence.json").unlink()
            (root / "report.md").write_bytes(b"\xff")
            self.assertEqual(self.cli(root).returncode, 2)

    def test_marker_phase_final_and_html_contract(self):
        html = COMMENT + "<footer>" + DRAFT_MARKER + "</footer>"
        for phase in ("shadow", "reviewed"):
            for file, good in (("report.md", GOOD), ("report.html", html)):
                self.assertNotIn("missing-draft-marker", {f["rule"] for f in inspect_artifact(file, good, phase)})
                for old in ("טיוטה — לא לשליחה", "DRAFT — NOT FOR SENDING", DRAFT_MARKER,
                            "<!-- arf:draft run= -->", "<!-- arf:draft run= -->" + DRAFT_MARKER):
                    with self.subTest(phase=phase, file=file, marker=old):
                        self.assertIn("missing-draft-marker", {f["rule"] for f in inspect_artifact(file, old, phase)})
                self.assertNotIn("missing-draft-marker", {f["rule"] for f in inspect_artifact(file, "", phase, final=True)})
        self.assertIn("missing-draft-marker", rules(COMMENT, "report.html"))
        self.assertNotIn("missing-draft-marker", {f["rule"] for f in inspect_artifact("report.html", "", "volume")})

    def test_pricing_role_implementation_and_time_boundaries(self):
        for phrase in ("הצעת מחיר", "תמחור", "עלות השירות", "מחירון", "price list", "pricing", "quote", "fee", "per hour", "hourly rate", "discount", "retainer"):
            self.assertIn("price-in-offer", rules(phrase, "notes-for-ben.md"))
        for phrase in ("50 ₪", "$ 50", "200 ש״ח", "NIS 30", "40 USD"):
            self.assertIn("price-in-offer", rules(phrase, "notes-for-ben.md"))
            self.assertIn("price-in-offer", rules(GOOD.replace("Let's talk.", phrase)))
            self.assertIn("currency-outside-offer", rules(GOOD.replace("Content.", phrase)))
        self.assertNotIn("price-in-offer", rules("בלי מחיר", "notes-for-ben.md"))
        for role in ("dev team manager", "development team manager", "engineering manager", "R&D manager", "team lead", "ראש צוות", "מנהל צוות פיתוח", "מנהל פיתוח", "ראש צוות פיתוח"):
            self.assertIn("ben-role", rules(role.upper(), "notes-for-ben.md"))
        self.assertNotIn("ben-role", rules("תקציר מנהלים; מי שמנהל את האתר", "notes-for-ben.md"))
        for token in ("application/ld+json", "@type", "Disallow:", "User-agent:", ".htaccess", "wp-config", "add the following", "הוסיפו את הקוד"):
            self.assertIn("implementation-detail", rules(GOOD + token))
            self.assertNotIn("implementation-detail", rules(token, "notes-for-ben.md"))
        for offer in ("First, install it. Then, test it.", "שלב אחר שלב", "Pillar\n    1. Nested instruction"):
            self.assertIn("detailed-offer-step", rules(GOOD.replace("Access policy.", offer)))
        self.assertNotIn("israel-time", rules(GOOD + "\n## Checks (Israel time)\n\n09:47 checked"))
        self.assertNotIn("israel-time", rules(GOOD + "\n09:47 Israel time"))
        self.assertIn("israel-time", rules(GOOD + "\n09:47 checked"))
        self.assertNotIn("blocked-single-source", rules(GOOD.replace("HTTP 403 and is-agentic blocked", "HTTP 403 blocked, not proven")))
        self.assertNotIn("blocked-single-source", rules(GOOD.replace("HTTP 403 and is-agentic blocked", "HTTP 403 חסום, לא עקבי")))

    def test_media_and_credentials(self):
        for media in ('<script>example()</script>', '<img src="https://example.test/a.png" width="100" height="100">',
                      '<iframe src="https://api.segment.io/a"></iframe>', '![x](https://example.test/a.png)',
                      '![x](local.png){width=1 height=0}'):
            self.assertIn("tracking-pixel", rules(media, "report.html", final=True))
        self.assertNotIn("tracking-pixel", rules('<img width="100" height="100" src="local.png">', "report.html", final=True))
        for code in ("    example", "~~~\nexample\n~~~", "```\nexample\n```", "`example`"):
            self.assertIn("code-block", rules(GOOD + code))
        self.assertIn("code-block", rules("<pre>example</pre>", "report.html", final=True))
        self.assertNotIn("code-block", rules("`example`", "notes-for-ben.md"))
        for credential in ("AKIAEXAMPLE0000000000", "sk-obviously_fake_123456789", "sk_live_obviously_fake_123456789",
                           "ghp_obviously_fake_123456789", "github_pat_obviously_fake_123456789", "xoxb-obviously-fake-123456789",
                           "-----BEGIN RSA PRIVATE KEY-----", "Bearer obviously_fake_123456789", "api_key='obviously_fake_123456789'", "YWJj" * 24):
            self.assertIn("secret", rules(credential, "notes-for-ben.md"))
        self.assertNotIn("secret", rules("password = short\nBearer short", "notes-for-ben.md"))
        for parameter in ("utm_campaign", "fbclid", "mc_eid", "msclkid"):
            self.assertIn("tracking-param", rules(f"https://example.test/?{parameter}=x", "notes-for-ben.md"))
        self.assertNotIn("tracking-param", rules("https://example.test/?campaign=x", "notes-for-ben.md"))

    def test_manifest_appendix_citations_pdf_and_skips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root, "report.md", GOOD)
            absent = lint(root)
            self.assertTrue(absent["passed"])
            self.assertTrue({"appendix-evidence", "non-evidence-url", "uncited-finding", "pdf-length", "prepared-by", "recipient-email", "employer-mention", "public-side-effect"}.issubset({s["rule"] for s in absent["skipped_checks"]}))
            paths = ("probes.csv", "discovery/0-0-0.body", "discovery/0-0-1.body")
            for path in paths:
                self.write(root, path, "Fetched https://evidence.test/page")
            self.write(root, "discovery.csv", "domain,path,url,status,size,body\nclient.test,/,https://evidence.test/page,200,1,discovery/0-0-0.body\n")
            self.write(root, "evidence.json", json.dumps(dict(site="https://client.test", hosts=["client.test"], files=[dict(path=p) for p in (*paths, "discovery.csv")])))
            self.write(root, "report.pdf", "/Type /Pages\n" + "/Type /Page\n" * 4 + DRAFT_MARKER)
            self.assertTrue(lint(root)["passed"])
            self.write(root, "report.pdf", "/Type /Page\n" * 5 + DRAFT_MARKER)
            self.assertIn("pdf-length", {f["rule"] for f in lint(root)["findings"]})
            self.write(root, "report.md", GOOD.replace("probes.csv | HTTP", "unknown.csv | HTTP"))
            self.assertIn("appendix-evidence", {f["rule"] for f in lint(root)["findings"]})
            self.write(root, "report.md", GOOD.replace("Jina observation", "[Jina](probes.csv)"))
            self.assertNotIn("uncited-finding", {f["rule"] for f in lint(root)["findings"]})
            self.write(root, "report.md", GOOD.replace("Jina observation", "[Jina](missing.csv)"))
            self.assertIn("uncited-finding", {f["rule"] for f in lint(root)["findings"]})
            self.write(root, "report.md", GOOD + "\nhttps://evidence.test/page")
            self.assertNotIn("non-evidence-url", {f["rule"] for f in lint(root)["findings"]})
            self.write(root, "report.md", GOOD + "\nhttps://unrelated.test/page")
            self.assertIn("non-evidence-url", {f["rule"] for f in lint(root)["findings"]})
            self.write(root, "probes.csv", "Captured HTML links to https://unrelated.test/page")
            self.assertIn("non-evidence-url", {f["rule"] for f in lint(root)["findings"]})
            self.write(root, "findings.json", '{"findings":[],"text":"Bearer obviously_fake_value_123456789"}')
            result = lint(root)
            self.assertIn("secret", {f["rule"] for f in result["findings"]})
            self.assertNotIn("uncited-finding", {s["rule"] for s in result["skipped_checks"]})

    def test_factory_config_and_contact_line(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root, "report.md", GOOD + "\nBen contact: ben@example.test")
            for extension, content in (("json", json.dumps(CONFIG)), ("yaml", "\n".join(f"{key}: '{value}'" for key, value in CONFIG.items()))):
                path = root / ("config." + extension)
                self.write(root, path.name, content)
                self.assertEqual(load_config(path), CONFIG)
                self.assertTrue(lint(root, factory_config=path)["passed"])
                self.assertEqual(self.cli(root, "--factory-config", path).returncode, 0)
            self.write(root, "report.md", GOOD + "\nTo: ben@example.test")
            self.assertIn("recipient-email", {f["rule"] for f in lint(root, factory_config=path)["findings"]})
            for bad in ("{}", "prepared_by: |\n  multiline", "prepared_by: one\nprepared_by: two"):
                self.write(root, path.name, bad)
                self.assertEqual(self.cli(root, "--factory-config", path).returncode, 2)

    def test_empty_and_structure_order(self):
        self.assertTrue({"required-headings", "missing-appendix", "missing-draft-marker"}.issubset(rules("")))
        self.assertIn("required-headings", rules(GOOD.replace("## 1. Why it matters", "## 3. Why it matters")))
        self.assertIn("findings-table", rules(GOOD.replace("| 2 | Jina observation | Readability |", "| 2 | Jina observation | Readability | extra |")))
        self.assertIn("sent-status", rules("סטטוס: נשלח", "notes-for-ben.md"))
        self.assertNotIn("sent-status", rules("This draft must not be sent.", "notes-for-ben.md"))

    def test_qa_unicode_pricing_and_control_probes(self):
        cases = [
            ("50＄", GOOD.replace("Talk.", "50＄"), "price-in-offer"),
            ("50 ＵＳＤ", GOOD.replace("Talk.", "50 ＵＳＤ"), "price-in-offer"),
            ("dev U+200B", GOOD + "\ndev\u200b team manager", "ben-role"),
            ("team U+200D", GOOD + "\ndev team\u200d manager", "ben-role"),
            ("niqqud", GOOD + "\nרֹאשׁ צֶוֶת", "ben-role"),
            ("fees", GOOD.replace("Talk.", "fees"), "price-in-offer"),
            ("discounts", GOOD.replace("Talk.", "discounts"), "price-in-offer"),
            ("50 USD", GOOD.replace("Talk.", "50 USD"), "price-in-offer"),
            ("lowercase image", GOOD + '<img src="https://example.test/a.png">', "tracking-pixel"),
            ("entity image", GOOD + '<img src="https&#58;//example.test/a.png">', "tracking-pixel"),
            ("uppercase image scheme", GOOD + '<img src="HTTPS://example.test/a.png">', "tracking-pixel"),
            ("tilde fence", GOOD + "\n~~~\nexample\n~~~", "code-block"),
            ("indented fence", GOOD + "\n    ```\n    example\n    ```", "code-block"),
            ("step by step", GOOD.replace("Access policy.", "First, open it. Then, change it."), "detailed-offer-step"),
        ]
        for phrase in ("price   list", "price\nlist", "price lists", "quotes", "retainers", "hourly   rates", "per\thour", "הצעת   מחיר", "עלות\nהשירות"):
            cases.append((phrase, GOOD.replace("Talk.", phrase), "price-in-offer"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, content, expected in cases:
                with self.subTest(probe=name):
                    self.write(root, "report.md", content)
                    process = self.cli(root)
                    self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
                    result = json.loads((root / "lint.json").read_text())
                    self.assertEqual({f["rule"] for f in result["findings"] if not f["acknowledged"]}, {expected})
            self.write(root, "report.md", GOOD)
            self.write(root, "report.html", HTML_GOOD)
            self.assertEqual(self.cli(root).returncode, 1)
            self.assertEqual({f["rule"] for f in lint(root)["findings"]}, {"missing-draft-marker"})
            self.write(root, "report.html", HTML_GOOD + "<footer>" + DRAFT_MARKER + "</footer>")
            self.assertEqual(self.cli(root).returncode, 0)

    def test_normalization_scope_and_stable_fingerprints(self):
        self.assertEqual(normalize_for_matching("רֹאשׁ\u200b צֶוֶת\u200d"), "ראש צות")
        self.assertEqual(normalize_for_matching("５０ ＵＳＤ"), "50 USD")
        self.assertEqual(normalize_for_matching("e\u0301"), "é")
        original = inspect_artifact("notes-for-ben.md", "dev team manager", "shadow")
        evasion = inspect_artifact("notes-for-ben.md", "\n\ndev\u200b team\u200d manager", "shadow")
        self.assertEqual(original[0]["fingerprint"], evasion[0]["fingerprint"])
        for role in ("ר\u0591אש צוות", "מְנַהֵל צֶוֶת פִּתּוּחַ", "dev\u2060 team manager", "dev&#8203; team manager"):
            self.assertIn("ben-role", rules(role, "report.html", final=True))

    def test_pdf_marker_raw_flate_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root, "report.md", GOOD)
            absent = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF"
            for phase in ("shadow", "reviewed"):
                for name, pdf, code in (
                    ("no marker", absent, 1),
                    ("empty PDF", b"", 1),
                    ("Flate without marker", pdf_stream(b"no marker"), 1),
                    ("corrupt Flate", b"<< /Filter /FlateDecode >>\nstream\nbroken\nendstream", 1),
                    ("raw UTF8", absent + DRAFT_MARKER.encode("utf-8"), 0),
                    ("raw UTF16BE", absent + DRAFT_MARKER.encode("utf-16-be"), 0),
                    ("Flate UTF8", pdf_stream(DRAFT_MARKER.encode("utf-8")), 0),
                    ("Flate UTF16BE", pdf_stream(DRAFT_MARKER.encode("utf-16-be")), 0),
                    ("Flate UTF16LE", pdf_stream(DRAFT_MARKER.encode("utf-16-le")), 0),
                ):
                    with self.subTest(phase=phase, pdf=name):
                        (root / "report.pdf").write_bytes(pdf)
                        process = self.cli(root, "--phase", phase)
                        self.assertEqual(process.returncode, code, process.stderr + process.stdout)
                        result = json.loads((root / "lint.json").read_text())
                        active = {f["rule"] for f in result["findings"]}
                        self.assertEqual(active, {"missing-draft-marker"} if code else set())
                        if code:
                            self.assertIn("could not be verified", result["findings"][0]["message"])
            (root / "report.pdf").write_bytes(absent)
            self.assertEqual(self.cli(root, "--final").returncode, 0)
            self.assertEqual(self.cli(root, "--phase", "volume").returncode, 0)

    def test_structured_findings_grounding_and_shapes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root, "report.md", GOOD)
            self.write(root, "probes.csv", "HTTP 403")
            data = {"findings": [{"id": 1, "evidence": ["probes.csv"]}]}
            self.write(root, "findings.json", json.dumps(data))
            self.assertEqual(self.cli(root).returncode, 0)
            result = lint(root)
            self.assertNotIn("uncited-finding", {s["rule"] for s in result["skipped_checks"]})
            self.assertIn("probes.csv", {i["file"] for i in result["inputs"]})
            for references in (["missing-evidence.txt"], [], ["../outside.txt"], [str(root / "probes.csv")]):
                with self.subTest(evidence=references):
                    data["findings"][0]["evidence"] = references
                    self.write(root, "findings.json", json.dumps(data))
                    process = self.cli(root)
                    self.assertEqual(process.returncode, 1, process.stderr)
                    self.assertEqual({f["rule"] for f in lint(root)["findings"]}, {"uncited-finding"})
            paths = ("probes.csv", "discovery/0-0-0.body", "discovery/0-0-1.body")
            for path in paths:
                self.write(root, path, "observation")
            self.write(root, "evidence.json", json.dumps({"files": [{"path": path} for path in paths]}))
            data["findings"][0]["evidence"] = ["probes.csv"]
            self.write(root, "findings.json", json.dumps(data))
            self.assertEqual(self.cli(root).returncode, 0)
            self.write(root, "unlisted.txt", "observation")
            for refs in (["unlisted.txt"], ["probes.csv", "missing-evidence.txt"]):
                data["findings"][0]["evidence"] = refs
                self.write(root, "findings.json", json.dumps(data))
                self.assertEqual(self.cli(root).returncode, 1)
                self.assertEqual({f["rule"] for f in lint(root)["findings"]}, {"uncited-finding"})
            (root / "probes.csv").unlink()
            data["findings"][0]["evidence"] = ["probes.csv"]
            self.write(root, "findings.json", json.dumps(data))
            self.assertEqual(self.cli(root).returncode, 1)
            for bad in ({}, [], {"findings": {}}, {"findings": [None]}, {"findings": [{"id": ""}]},
                        {"findings": [{"id": 1, "evidence": "probes.csv"}]},
                        {"findings": [{"id": True, "evidence": []}]},
                        {"findings": [{"id": 1, "evidence": [4]}]},
                        {"findings": [{"id": 1, "evidence": []}, {"id": "1", "evidence": []}]}):
                with self.subTest(shape=bad):
                    self.write(root, "findings.json", json.dumps(bad))
                    self.assertEqual(self.cli(root).returncode, 2)
            self.write(root, "findings.json", '{"findings": []}')
            self.assertEqual(self.cli(root).returncode, 0)

    def test_deterministic_with_optional_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root, "report.md", GOOD)
            paths = ("probes.csv", "discovery/0-0-0.body", "discovery/0-0-1.body")
            for path in paths:
                self.write(root, path, "observation")
            self.write(root, "findings.json", json.dumps({"findings": [{"id": 1, "evidence": ["probes.csv"]}]}))
            self.write(root, "evidence.json", json.dumps({"files": [{"path": path} for path in paths]}))
            (root / "report.pdf").write_bytes(pdf_stream(DRAFT_MARKER.encode("utf-8")))
            self.assertEqual(self.cli(root).returncode, 0)
            first = (root / "lint.json").read_bytes()
            self.assertEqual(self.cli(root).returncode, 0)
            self.assertEqual((root / "lint.json").read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
