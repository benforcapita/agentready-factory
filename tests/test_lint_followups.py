import json
from pathlib import Path
import tempfile
import unittest

from arf.lint.markers import DRAFT_MARKER
from tests import test_lint as lint_helpers


ROOT = Path(__file__).resolve().parents[1]
PROBES = json.loads((ROOT / "fixtures/lint/followups/probes.json").read_text(encoding="utf-8"))


class LintFollowupTests(unittest.TestCase):
    def lint_cli(self, root):
        return lint_helpers.LintTests().cli(root, "--source-only")

    def write(self, root, path, content):
        lint_helpers.LintTests().write(root, path, content)

    def output(self, root):
        return json.loads((root / "lint.json").read_text(encoding="utf-8"))

    def assert_bidi_override_cli_fails(self, root, target_file):
        first = self.lint_cli(root)
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        first_bytes = (root / "lint.json").read_bytes()
        result = json.loads(first_bytes)
        findings = [f for f in result["findings"] if f["rule"] == "bidi-override"]
        self.assertTrue(findings, result)
        for finding in findings:
            self.assertEqual(finding["level"], "hard_fail")
            self.assertFalse(finding["acknowledged"])
            self.assertIn(finding["excerpt"], {"U+202D", "U+202E"})
            self.assertIn("remove", finding["message"].lower())
            self.assertIn("override", finding["message"].lower())
        serialized = first_bytes.decode("utf-8")
        self.assertNotIn("\u202d", serialized)
        self.assertNotIn("\u202e", serialized)
        self.assertEqual(self.lint_cli(root).returncode, 1)
        self.assertEqual((root / "lint.json").read_bytes(), first_bytes)

        finding = next(f for f in findings if f["file"] == target_file)
        self.write(root, "lint-ack.json", json.dumps({"version": 1, "acks": [{
            "rule": finding["rule"], "file": finding["file"],
            "fingerprint": finding["fingerprint"], "reason": "Exercise hard-fail acknowledgement handling"
        }]}))
        acknowledged = self.lint_cli(root)
        self.assertEqual(acknowledged.returncode, 1, acknowledged.stdout + acknowledged.stderr)
        after = self.output(root)
        active = [f for f in after["findings"] if f["rule"] == "bidi-override"]
        self.assertTrue(active)
        self.assertTrue(all(f["level"] == "hard_fail" and not f["acknowledged"] for f in active))
        self.assertIn("hard-fail-ack-ignored", {ack["status"] for ack in after["unused_acks"]})

    def test_n1_punctuation_is_fail_closed(self):
        for probe in PROBES["n1"]["hard_fail"]:
            with self.subTest(probe=probe):
                self.assertIn("price-in-offer", lint_helpers.rules(probe, "notes-for-ben.md"))

    def test_n3_documented_and_existing_phrase_variants(self):
        for case in PROBES["n3"]["hard_fail"]:
            with self.subTest(probe=case["text"]):
                self.assertIn(case["rule"], lint_helpers.rules(case["text"], "notes-for-ben.md"))
        for case in PROBES["n3"]["negative_pins"]:
            with self.subTest(probe=case["text"]):
                self.assertEqual(lint_helpers.rules(case["text"], "notes-for-ben.md"), set(case["rules"]))

    def test_n4_b2_negatives_and_word_boundaries_pass(self):
        for probe in PROBES["n4"]["b2_no_hard_fail"] + PROBES["n4"]["ordinary_word_boundaries_pass"]:
            with self.subTest(probe=probe):
                found = lint_helpers.rules(probe, "notes-for-ben.md")
                self.assertNotIn("ben-role", found)
                self.assertNotIn("price-in-offer", found)
        for case in PROBES["n4"]["warning_only"]:
            with self.subTest(probe=case["text"]):
                self.assertEqual(lint_helpers.rules(case["text"], "notes-for-ben.md"), {case["rule"]})

    def test_n4_semantic_ambiguities_remain_hard_fail(self):
        self.assertEqual(PROBES["n4"]["semantic_judgment_deferred_to"], "PRO-405")
        for case in PROBES["n4"]["intentionally_hard_fail"]:
            with self.subTest(probe=case["text"]):
                self.assertIn(case["rule"], lint_helpers.rules(case["text"], "notes-for-ben.md"))

    def test_n5_controls_alone_and_inside_banned_phrases(self):
        for control in PROBES["n5"]["control_matrix"]:
            char = chr(int(control["codepoint"], 16))
            with self.subTest(control=control["name"], mode="alone"):
                alone = lint_helpers.rules(lint_helpers.GOOD + "\n" + char, "report.md")
                if control["disposition"] == "reject":
                    self.assertIn("bidi-override", alone)
                else:
                    self.assertNotIn("bidi-override", alone)
            for phrase, rule in (("dev team manager", "ben-role"), ("price list", "price-in-offer")):
                probe = phrase[0] + char + phrase[1:]
                with self.subTest(control=control["name"], phrase=phrase):
                    found = lint_helpers.rules(probe, "notes-for-ben.md")
                    self.assertIn(rule, found)
                    if control["disposition"] == "reject":
                        self.assertIn("bidi-override", found)

        for case in PROBES["n5"]["existing_confusable_pins"]:
            with self.subTest(confusable=case["text"]):
                self.assertIn(case["rule"], lint_helpers.rules(case["text"], "notes-for-ben.md"))

    def test_n5_raw_markdown_overrides_fail_public_cli(self):
        for case in PROBES["n5"]["raw_override_payloads"]:
            with self.subTest(payload=case["id"]), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.write(root, "report.md", lint_helpers.GOOD + "\n\n" + case["text"])
                self.assert_bidi_override_cli_fails(root, "report.md")

    def test_n5_html_numeric_entities_fail_public_cli(self):
        for case in PROBES["n5"]["html_numeric_entities"]:
            with self.subTest(entity=case["entity"]), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.write(root, "report.md", lint_helpers.GOOD)
                html = lint_helpers.HTML_GOOD + f"<footer>{DRAFT_MARKER}</footer><p>Control {case['entity']} here.</p>"
                self.write(root, "report.html", html)
                self.assert_bidi_override_cli_fails(root, "report.html")

    def test_existing_wider_currency_support_remains_pinned(self):
        for probe in PROBES["currencies"]["current_support_pins"]:
            with self.subTest(probe=probe):
                self.assertIn("price-in-offer", lint_helpers.rules(lint_helpers.GOOD.replace("Talk.", probe), "report.md"))


if __name__ == "__main__":
    unittest.main()
