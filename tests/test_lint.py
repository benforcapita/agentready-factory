import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from arf.lint import inspect_artifact, lint
from arf.lint.markers import DRAFT_MARKER_EN

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures/lint"
GOLDEN = ROOT / "fixtures/golden/lehamim"
GOOD = DRAFT_MARKER_EN + "\n## 2. Findings\n| 1 | HTTP 403 and is-agentic blocked | impact |\n## 4. Offer\n1. Review\n## 5. Next\nTalk\n## Appendix\n| Source | Result |\n|---|---|\n| HTTP | OK |\n"


class LintTests(unittest.TestCase):
    def cli(self, directory, *args):
        return subprocess.run([sys.executable, "-m", "arf.lint", str(directory), *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True)

    def test_each_rule_positive_and_negative(self):
        cases = {
            "price-in-offer": GOOD.replace("Talk", "pricing: £20"),
            "code-block": GOOD + "\n`example`\n",
            "ben-role": GOOD + "\nBen is a team lead",
            "tracking-pixel": GOOD + '<img style="width:0px;height:1px" src="local.gif">',
            "tracking-param": GOOD + "https://example.com/?gclid=abc",
            "missing-appendix": GOOD.replace("## Appendix", "## Sources"),
            "missing-draft-marker": GOOD.replace(DRAFT_MARKER_EN, ""),
            "secret": GOOD + "\npassword = obviously_fake_value_123456789",
            "blocked-single-source": GOOD.replace(" and is-agentic", ""),
            "extra-offer-steps": GOOD.replace("1. Review", "1. A\n2. B\n3. C\n4. D\n5. E"),
            "uncited-finding": GOOD.replace("HTTP 403 and is-agentic blocked", "Observation"),
            "placeholder-left": GOOD + "[CONTACT_NAME]",
        }
        for rule, content in cases.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, {f["rule"] for f in inspect_artifact("report.md", content, "shadow")})
                self.assertNotIn(rule, {f["rule"] for f in inspect_artifact("report.md", GOOD, "shadow")})

    def test_lehamim_and_expected(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "lint.json"
            process = self.cli(GOLDEN, "--phase", "reviewed", "--ack", FIXTURES / "lehamim/lint-ack.json", "--out", out)
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            self.assertEqual(out.read_bytes(), (FIXTURES / "expected/lehamim.json").read_bytes())
            self.assertEqual(json.loads(out.read_text())["summary"]["hard_fail"], 0)

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

    def test_acks_and_line_stability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "report.md"
            report.write_text(GOOD + "[CONTACT_NAME]\nBen is a team lead", encoding="utf-8")
            findings = lint(root)["findings"]
            acks = [{**{key: f[key] for key in ("rule", "file", "fingerprint")}, "reason": "Test acknowledgement"} for f in findings]
            acks.append(dict(rule="placeholder-left", file="report.md", fingerprint="unused", reason="Old finding"))
            (root / "lint-ack.json").write_text(json.dumps(dict(version=1, acks=acks)))
            report.write_text("\n\n" + GOOD + "[CONTACT_NAME]\nBen is a team lead", encoding="utf-8")
            result = lint(root)
            self.assertEqual(result["summary"], dict(hard_fail=1, warning=1, acknowledged=1, unacknowledged_warning=0))
            self.assertEqual({a["status"] for a in result["unused_acks"]}, {"unused", "hard-fail-ack-ignored"})
            report.write_text(GOOD + "[CONTACT_NAME]", encoding="utf-8")
            self.assertEqual(self.cli(root).returncode, 0)
            self.assertTrue((root / "lint.json").exists())

    def test_input_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(self.cli(root).returncode, 2)
            (root / "report.md").write_text(GOOD)
            for content in ("{", "[]", '{"version":2,"acks":[]}', '{"version":1,"acks":[{}]}'):
                (root / "lint-ack.json").write_text(content)
                self.assertEqual(self.cli(root).returncode, 2)
            (root / "lint-ack.json").unlink()
            self.assertEqual(self.cli(root, "--phase", "unknown").returncode, 2)
            self.assertEqual(self.cli(root, "--ack", root / "missing.json").returncode, 2)
            self.assertEqual(self.cli(root, "--out", root / "report.md").returncode, 2)
            self.assertEqual(self.cli(root, "--out", root / "missing/lint.json").returncode, 2)
            (root / "report.md").write_bytes(b"\xff")
            self.assertEqual(self.cli(root).returncode, 2)

    def test_artifact_scope_and_variants(self):
        self.assertEqual(inspect_artifact("report.md", GOOD, "shadow"), [])
        for phase in ("reviewed", "volume"):
            self.assertEqual(inspect_artifact("report.md", GOOD.replace(DRAFT_MARKER_EN, ""), phase), [])
        self.assertEqual(inspect_artifact("notes-for-ben.md", "`example`\nמחיר 50", "shadow"), [])
        for text in ('<script src="https://www.googletagmanager.com/a.js"></script>',
                     '![x](https://api.segment.io/x.png)', '![x](local.png){width=1 height=0}'):
            self.assertIn("tracking-pixel", {f["rule"] for f in inspect_artifact("report.html", text, "reviewed")})
        self.assertEqual(inspect_artifact("report.html", '<img width="100" height="100" src="https://example.com/a.png">', "reviewed"), [])
        for text in ("dev team manager", "development team manager", "מנהל צוות פיתוח"):
            self.assertIn("ben-role", {f["rule"] for f in inspect_artifact("notes-for-ben.md", text, "shadow")})
        for code in ("    example", "~~~\nexample\n~~~", "```\nexample\n```"):
            self.assertIn("code-block", {f["rule"] for f in inspect_artifact("report.md", GOOD + code, "shadow")})
        self.assertIn("missing-appendix", {f["rule"] for f in inspect_artifact("report.md", "", "shadow")})
        self.assertNotIn("price-in-offer", {f["rule"] for f in inspect_artifact("report.md", GOOD.replace("## 2. Findings", "## 2. Findings\nprice 200 ₪"), "shadow")})

    def test_credentials_and_tracking_variants(self):
        for credential in ("AKIAEXAMPLE0000000000", "sk-obviously_fake_123456789",
                           "sk_live_obviously_fake_123456789", "ghp_obviously_fake_123456789",
                           "github_pat_obviously_fake_123456789", "xoxb-obviously-fake-123456789",
                           "-----BEGIN RSA PRIVATE KEY-----", "Bearer obviously_fake_123456789",
                           "api_key='obviously_fake_123456789'"):
            with self.subTest(credential=credential):
                self.assertIn("secret", {f["rule"] for f in inspect_artifact("notes-for-ben.md", credential, "volume")})
        self.assertEqual(inspect_artifact("notes-for-ben.md", "password = short\nBearer short", "volume"), [])
        for parameter in ("utm_campaign", "fbclid", "mc_eid", "msclkid"):
            self.assertIn("tracking-param", {f["rule"] for f in inspect_artifact("notes-for-ben.md", f"https://example.com/?{parameter}=x", "volume")})
        self.assertEqual(inspect_artifact("notes-for-ben.md", "https://example.com/?campaign=x", "volume"), [])


if __name__ == "__main__":
    unittest.main()
