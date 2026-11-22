from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from arf.render import DRAFT_MARKER, BlockedResource, draft_comment, render_html, render_pdf
from arf.render.__main__ import main


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "fixtures" / "golden" / "lehamim"
ENGLISH = ROOT / "fixtures" / "render" / "en" / "report.md"
GOLDEN_TITLE = "דוח AgentReady — מאפיית לחמים"
ENTITY_REPORT = "# בדיקה\n\nשלום AT&T ו־Ben & Jerry's ו־A < B ו־A > B ו־https://example.com/?a=1&b=2.\n"
ENTITY_TEXTS = ("AT&T", "Ben & Jerry's", "A < B", "A > B", "https://example.com/?a=1&b=2")
POPPLER = all(shutil.which(tool) for tool in ("pdfinfo", "pdffonts", "pdftotext"))
BIDI_CONTROLS = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def normalize(document: str) -> str:
    document = re.sub(r">\s+<", "><", document)
    return re.sub(r"\s+", " ", document).strip()


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


class RenderCommand:
    def render(self, *args):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([str(arg) for arg in args])
        return code, stdout.getvalue(), stderr.getvalue()


class HtmlTests(RenderCommand, unittest.TestCase):
    def setUp(self):
        self.out = Path(self.enterContext(TemporaryDirectory()))

    def test_golden_html_matches_lehamim_with_marker_off(self):
        code, _, stderr = self.render(
            GOLDEN / "report.md", "--out", self.out, "--title", GOLDEN_TITLE, "--final", "--no-pdf"
        )
        self.assertEqual(code, 0, stderr)
        rendered = (self.out / "report.html").read_text(encoding="utf-8")
        golden = (GOLDEN / "report.html").read_text(encoding="utf-8")
        self.assertEqual(normalize(rendered), normalize(golden))
        self.assertEqual(rendered, golden)
        self.assertFalse((self.out / "report.pdf").exists())

    def test_draft_marker_is_on_by_default(self):
        code, _, stderr = self.render(GOLDEN / "report.md", "--out", self.out, "--run-id", "run-7", "--no-pdf")
        self.assertEqual(code, 0, stderr)
        rendered = (self.out / "report.html").read_text(encoding="utf-8")
        self.assertIn('<body dir="rtl">\n<!-- arf:draft run=run-7 -->\n<h1>', rendered)
        self.assertIn(f'<footer class="draft-marker" role="note">{DRAFT_MARKER}</footer>\n</body>', rendered)
        self.assertIn(f'@bottom-center{{content:"{DRAFT_MARKER}"', rendered)
        self.assertEqual(rendered.count("arf:draft"), 1)

    def test_run_id_is_read_from_the_report_comment(self):
        rendered = render_html("<!-- arf:draft run=2026-11-22T07:40_lehamim -->\n# x\n", lang="en")
        self.assertIn("<!-- arf:draft run=2026-11-22T07:40_lehamim -->", rendered)
        self.assertEqual(rendered.count("arf:draft"), 1)
        same = render_html("<!-- arf:draft run=a1 -->\n# x\n", lang="en", run_id="a1")
        self.assertEqual(same.count("arf:draft run=a1"), 1)

    def test_draft_requires_one_valid_run_id(self):
        code, _, stderr = self.render(GOLDEN / "report.md", "--out", self.out, "--no-pdf")
        self.assertEqual(code, 1)
        self.assertIn("needs a run id", stderr)
        self.assertFalse((self.out / "report.html").exists())
        with self.assertRaisesRegex(ValueError, "conflicting"):
            render_html("<!-- arf:draft run=a1 -->\n# x\n", run_id="b2")
        for bad in ("", "a b", "x-->", "-x"):
            with self.assertRaisesRegex(ValueError, "invalid run id"):
                draft_comment(bad)
        self.assertEqual(draft_comment("r1"), "<!-- arf:draft run=r1 -->")

    def test_final_strips_the_draft_comment(self):
        rendered = render_html("<!-- arf:draft run=a1 -->\n# x\n", lang="en", draft_marker=False)
        self.assertNotIn("arf:draft", rendered)
        self.assertNotIn(DRAFT_MARKER, rendered)

    def test_draft_marker_text_is_the_lld_marker(self):
        self.assertEqual(DRAFT_MARKER, "\u05d8\u05d9\u05d5\u05d8\u05d4 \u2014 \u05dc\u05d0 \u05dc\u05e9\u05dc\u05d9\u05d7\u05d4 \u00b7 DRAFT")

    def test_no_draft_marker_removes_every_marker(self):
        rendered = render_html((GOLDEN / "report.md").read_text(encoding="utf-8"), draft_marker=False)
        self.assertNotIn(DRAFT_MARKER, rendered)
        self.assertNotIn("draft-marker", rendered)

    def test_title_defaults_to_first_heading(self):
        rendered = render_html("# Report for *Acme* & co\n\nBody\n", lang="en", run_id="t")
        self.assertIn("<title>Report for Acme &amp; co</title>", rendered)

    def test_hebrew_wraps_latin_runs_but_not_placeholders(self):
        rendered = render_html("שלום ChatGPT, ‏Gemini ו־[CONTACT_NAME] ב־example.com.\n", draft_marker=False)
        self.assertIn('שלום <span dir="ltr" class="en">ChatGPT, Gemini</span> ו־', rendered)
        self.assertIn("ו־[CONTACT_NAME]", rendered)
        self.assertIn('<span dir="ltr" class="en">example.com</span>.', rendered)
        self.assertNotIn("\u200f", rendered)

    def test_hebrew_latin_wrapping_keeps_html_entities_whole(self):
        rendered = render_html(ENTITY_REPORT, draft_marker=False)
        for wrapped in ("AT&amp;T", "Ben &amp; Jerry's", "A &lt; B", "A &gt; B", "https://example.com/?a=1&amp;b=2"):
            self.assertIn(f'<span dir="ltr" class="en">{wrapped}</span>', rendered)
        self.assertNotRegex(rendered, r"&(amp|lt|gt)</span>|</span>;|&amp;amp;")
        self.assertNotIn('class="en">amp<', rendered)

    def test_hebrew_html_comments_with_gt_do_not_swallow_the_report(self):
        source = "# בדיקה\n\n<!-- note: score > 3 -->\n\n<!-- source: A -> B -->\n\nפסקה חשובה ABC אחרי ההערה\n\n## סעיף נוסף\n\nעוד טקסט\n"
        rendered = render_html(source, draft_marker=False)
        self.assertIn("<!-- note: score > 3 -->", rendered)
        self.assertIn("<!-- source: A -> B -->", rendered)
        self.assertNotIn("--&gt;", rendered)
        self.assertIn('פסקה חשובה <span dir="ltr" class="en">ABC</span> אחרי ההערה', rendered)
        self.assertIn("<h2>סעיף נוסף</h2>", rendered)
        self.assertIn("עוד טקסט", rendered)

    def test_english_is_ltr_without_latin_wrapping(self):
        code, _, stderr = self.render(ENGLISH, "--out", self.out, "--lang", "en", "--no-pdf")
        self.assertEqual(code, 0, stderr)
        rendered = (self.out / "report.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="en" dir="ltr">', rendered)
        self.assertIn('<body dir="ltr">', rendered)
        self.assertIn('<table dir="ltr">', rendered)
        self.assertIn("text-align:left", rendered)
        self.assertIn('<section class="summary"><h2>Executive summary</h2>', rendered)
        self.assertIn(DRAFT_MARKER, rendered)
        self.assertIn("<!-- arf:draft run=fixture-en -->", rendered)
        self.assertNotIn('class="en"', rendered.split("<body", 1)[1])

    def test_html_has_no_external_resources(self):
        for lang, source in (("he", GOLDEN / "report.md"), ("en", ENGLISH)):
            rendered = render_html(source.read_text(encoding="utf-8"), lang=lang, run_id="fixture-en")
            self.assertNotRegex(rendered, r"(?i)<(script|img|link|iframe)\b|https?://|url\(")

    def test_unsupported_language_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.render(ENGLISH, "--out", self.out, "--lang", "fr")
        with self.assertRaisesRegex(ValueError, "unsupported language"):
            render_html("# x\n", lang="fr")

    def test_missing_report_fails(self):
        code, stdout, stderr = self.render(self.out / "missing.md", "--out", self.out)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("arf render:", stderr)

    def test_module_entry_point(self):
        result = subprocess.run(
            [sys.executable, "-m", "arf.render", "--help"], capture_output=True, text=True, check=True
        )
        self.assertIn("--final", result.stdout)
        self.assertIn("--run-id", result.stdout)


@unittest.skipUnless(POPPLER or os.environ.get("CI"), "poppler-utils not installed")
class PdfTests(RenderCommand, unittest.TestCase):
    def setUp(self):
        self.out = Path(self.enterContext(TemporaryDirectory()))

    def render_pdf_fixture(self, source, *args):
        code, stdout, stderr = self.render(source, "--out", self.out, *args)
        self.assertEqual(code, 0, stderr)
        self.assertIn("report.pdf", stdout)
        return self.out / "report.pdf"

    def assert_a4_with_embedded_fonts(self, pdf, min_pages, max_pages):
        info = run("pdfinfo", str(pdf))
        self.assertRegex(info, r"Page size:\s+595\.\d+ x 841\.\d+ pts \(A4\)")
        pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
        self.assertGreaterEqual(pages, min_pages)
        self.assertLessEqual(pages, max_pages)
        fonts = run("pdffonts", str(pdf)).splitlines()[2:]
        self.assertTrue(fonts)
        for line in fonts:
            self.assertEqual(line.split()[-5], "yes", f"font not embedded: {line}")

    def text(self, pdf):
        return BIDI_CONTROLS.sub("", run("pdftotext", "-layout", str(pdf), "-"))

    def test_lehamim_pdf_is_a4_2_to_4_pages_with_embedded_fonts_and_hebrew_text(self):
        pdf = self.render_pdf_fixture(GOLDEN / "report.md", "--title", GOLDEN_TITLE, "--no-draft-marker")
        self.assert_a4_with_embedded_fonts(pdf, 2, 4)
        text = self.text(pdf)
        for phrase in ("תקציר מנהלים", "מאפיית לחמים", "מה מצאנו", "הצעד הבא"):
            self.assertIn(phrase, text)
        self.assertNotIn("DRAFT", text)

    def test_shadow_pdf_carries_the_draft_marker_on_every_page(self):
        pdf = self.render_pdf_fixture(GOLDEN / "report.md", "--run-id", "run-7")
        pages = self.text(pdf).split("\f")
        pages = [page for page in pages if page.strip()]
        self.assertGreaterEqual(len(pages), 2)
        for page in pages:
            self.assertIn("טיוטה — לא לשליחה", page)
            self.assertIn("DRAFT", page)

    def test_english_pdf(self):
        pdf = self.render_pdf_fixture(ENGLISH, "--lang", "en")
        self.assert_a4_with_embedded_fonts(pdf, 1, 4)
        self.assertIn("Executive summary", self.text(pdf))

    def test_hebrew_pdf_text_keeps_ampersands_angle_brackets_and_query_strings(self):
        source = self.out / "entity.md"
        source.write_text(ENTITY_REPORT, encoding="utf-8")
        text = self.text(self.render_pdf_fixture(source, "--lang", "he", "--final"))
        for phrase in ENTITY_TEXTS:
            self.assertIn(phrase, text)

    def test_hebrew_pdf_renders_content_after_html_comments_with_gt(self):
        source = self.out / "comment.md"
        source.write_text("# בדיקה\n\n<!-- source: A -> B -->\n\nפסקה חשובה ABC אחרי ההערה\n\n## סעיף נוסף\n\nעוד טקסט\n", encoding="utf-8")
        text = self.text(self.render_pdf_fixture(source, "--lang", "he", "--final"))
        for phrase in ("פסקה חשובה", "ABC", "סעיף נוסף", "עוד טקסט"):
            self.assertIn(phrase, text)

    def test_external_resources_are_refused(self):
        pdf = self.out / "report.pdf"
        document = render_html('# x\n\n<img src="https://tracker.example/p.gif">\n', lang="en", run_id="t")
        with self.assertRaisesRegex(BlockedResource, "tracker.example"):
            render_pdf(document, pdf)
        self.assertFalse(pdf.exists())


if __name__ == "__main__":
    unittest.main()
