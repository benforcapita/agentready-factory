from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from arf.prompts import RecordedPromptVersions
from arf.scaffold import ScaffoldError, render, scaffold, slots
from arf.scaffold.__main__ import load_request, main
from arf.scaffold.templates import NOTES, REPORT


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "scaffold"
PROMPTS = ROOT / "fixtures" / "prompts"
GOLDEN = ROOT / "fixtures" / "golden" / "lehamim"
RUN_ID = "arf-example-com-20261120-1000-ab12"
OUTPUTS = ("report.md", "notes-for-ben.md", "prompts-used.json")
REPORT_SLOTS = [
    "check-date", "bottom-line", "external-score", "good-news", "proposal", "why-it-matters",
    "findings-rows", "what-works", "offer", "next-step", "sources-rows", "scope-note",
]
NOTES_SLOTS = ["check-date", "talking-points", "questions", "caveats"]


def request(language="he"):
    return load_request(FIXTURES / f"request-{language}.yaml")


def h2(text):
    return [line[3:].split(" (")[0] for line in text.splitlines() if line.startswith("## ")]


def files(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


class TemplateTest(unittest.TestCase):
    def test_slots_match_across_languages(self):
        for language in ("he", "en"):
            with self.subTest(language):
                self.assertEqual(slots(REPORT[language]), REPORT_SLOTS)
                self.assertEqual(slots(NOTES[language]), NOTES_SLOTS)

    def test_hebrew_skeleton_follows_golden_structure(self):
        result = render(request().model_copy(update={"business_name": "לחמים"}), RUN_ID)
        self.assertEqual(h2(result.report), h2((GOLDEN / "report.md").read_text(encoding="utf-8")))
        golden_notes = (GOLDEN / "notes-for-ben.md").read_text(encoding="utf-8").replace("[CONTACT_NAME]", "Example Contact")
        self.assertEqual(h2(result.notes), h2(golden_notes))

    def test_english_skeleton_has_the_same_sections(self):
        he, en = render(request(), RUN_ID), render(request("en"), RUN_ID)
        self.assertEqual(len(h2(en.report)), len(h2(he.report)))
        numbered = lambda text: [title[:2] if title[0].isdigit() else "" for title in h2(text)]
        self.assertEqual(numbered(en.report), numbered(he.report))
        self.assertEqual(len(h2(en.notes)), len(h2(he.notes)))

    def test_draft_marker(self):
        shadow = render(request(), RUN_ID)
        self.assertTrue(shadow.report.startswith(f"<!-- arf:draft run={RUN_ID} -->\n# "))
        self.assertNotIn("arf:draft", shadow.notes)
        volume = render(request().model_copy(update={"mode": "volume"}), RUN_ID)
        self.assertNotIn("arf:draft", volume.report)

    def test_language_and_fallbacks(self):
        self.assertEqual(render(request(), RUN_ID).language, "he")
        self.assertEqual(render(request(), RUN_ID, "en").language, "en")
        bare = render(request("en").model_copy(update={"business_name": None, "contact": None}), RUN_ID)
        self.assertIn("— example.com\n", bare.report)
        self.assertIn("call with the contact (example.com)", bare.notes)
        with self.assertRaisesRegex(ScaffoldError, "invalid run id"):
            render(request(), "../escape")

    def test_meeting_is_israel_time(self):
        result = render(request(), RUN_ID)
        self.assertIn("**לקראת שיחה:** יום שני, 23.11.2026, 10:00 (שעון ישראל), Google Meet", result.report)
        utc = request("en").model_copy(update={"meeting": request().meeting.model_copy(
            update={"start": datetime(2026, 6, 1, 7, 30, tzinfo=timezone.utc)})})
        self.assertIn("**For the call:** Monday, 1 June 2026, 10:30 (Israel time), Google Meet", render(utc, RUN_ID).report)
        self.assertNotRegex(render(request("en"), RUN_ID).report, r"\d{1,2}:\d{2}")

    def test_no_prices_or_tracking(self):
        for template in (*REPORT.values(), *NOTES.values()):
            self.assertNotRegex(template, r"₪|\$|€|ש\"ח|price|מחיר|<script|<img|https?://")


class ScaffoldTest(unittest.TestCase):
    def test_matches_recorded_outputs(self):
        for language in ("he", "en"):
            with self.subTest(language), TemporaryDirectory() as directory:
                out = Path(directory) / "run"
                scaffold(request(language), out, RUN_ID, RecordedPromptVersions(PROMPTS / "recorded"))
                expected = FIXTURES / "expected" / language
                self.assertEqual(files(out), files(expected))
                for name in files(expected):
                    self.assertEqual((out / name).read_bytes(), (expected / name).read_bytes(), name)

    def test_request_overrides_are_used(self):
        data = json.loads((FIXTURES / "expected" / "en" / "prompts-used.json").read_text(encoding="utf-8"))
        analyze = data["prompts"][0]
        self.assertEqual((analyze["step"], analyze["selected_by"], analyze["status"]), ("analyze", "override", "testing"))
        self.assertEqual(analyze["row_url"], str(request("en").prompt_overrides["analyze"]))

    def test_selection_error_writes_nothing(self):
        with TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            with self.assertRaisesRegex(Exception, "PromptSelectionError|expected exactly one"):
                scaffold(request(), out, RUN_ID, RecordedPromptVersions(PROMPTS / "two-production"))
            self.assertFalse(out.exists())

    def test_refuses_non_empty_output(self):
        with TemporaryDirectory() as directory:
            (Path(directory) / "report.md").write_text("keep")
            with self.assertRaisesRegex(ScaffoldError, "not empty"):
                scaffold(request(), Path(directory), RUN_ID, RecordedPromptVersions(PROMPTS / "recorded"))
            self.assertEqual((Path(directory) / "report.md").read_text(), "keep")


class CommandTest(unittest.TestCase):
    def run_main(self, *args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([str(arg) for arg in args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_writes_run_folder(self):
        with TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            code, stdout, _ = self.run_main(
                FIXTURES / "request-en.yaml", "--out", out, "--run-id", RUN_ID, "--prompts", PROMPTS / "recorded", "--lang", "he"
            )
            self.assertEqual(code, 0)
            self.assertIn("(he)", stdout)
            self.assertTrue(all((out / name).is_file() for name in OUTPUTS))
            self.assertEqual((out / "report.md").read_text(encoding="utf-8").count("arf:slot"), len(REPORT_SLOTS))

    def test_prompt_selection_error_exit(self):
        with TemporaryDirectory() as directory:
            code, _, stderr = self.run_main(
                FIXTURES / "request-he.yaml", "--out", Path(directory) / "run", "--run-id", RUN_ID, "--prompts", PROMPTS / "zero-production"
            )
            self.assertEqual(code, 1)
            self.assertRegex(stderr, r"^PromptSelectionError: write-notes: ")

    def test_invalid_request_exit(self):
        with TemporaryDirectory() as directory:
            bad = Path(directory) / "request.yaml"
            bad.write_text("version: 2\n")
            code, _, stderr = self.run_main(bad, "--out", Path(directory) / "run", "--run-id", RUN_ID, "--prompts", PROMPTS / "recorded")
            self.assertEqual(code, 1)
            self.assertTrue(re.match(r"error: invalid request: ", stderr))


if __name__ == "__main__":
    unittest.main()
