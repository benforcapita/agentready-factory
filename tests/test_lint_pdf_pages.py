"""pdf-length page counting on object-stream PDFs (PRO-734, PRO-727 LLD §2).

The fixtures are WeasyPrint 70 renders of plain "page N of M" text: their page
objects live only inside compressed /ObjStm streams, like T5's report.pdf.
"""

import re
import tempfile
import unittest
from pathlib import Path

from arf.lint import lint, pdf_page_count
from tests.test_lint import GOOD

PAGES = Path(__file__).resolve().parents[1] / "fixtures/lint/pdf-pages"
SKIP = "report.pdf has no readable /Type /Page objects; compressed object streams are not decoded."


class PDFPageCountTest(unittest.TestCase):
    def lint_pdf(self, pdf):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "report.md").write_text(GOOD, encoding="utf-8")
            (root / "report.pdf").write_bytes(pdf)
            result = lint(root, render=False)
        findings = [item for item in result["findings"] if item["rule"] == "pdf-length"]
        skipped = [item for item in result["skipped_checks"] if item["rule"] == "pdf-length"]
        return findings, skipped

    def test_fixtures_hide_pages_in_object_streams(self):
        for pages in (3, 5):
            pdf = (PAGES / f"objstm-{pages}-pages.pdf").read_bytes()
            with self.subTest(pages=pages):
                self.assertIn(b"/ObjStm", pdf)
                self.assertEqual(re.findall(rb"/Type\s*/Page\b", pdf), [])
                self.assertEqual(pdf_page_count(pdf), pages)

    def test_three_page_object_stream_pdf_is_counted_without_skip(self):
        findings, skipped = self.lint_pdf((PAGES / "objstm-3-pages.pdf").read_bytes())
        self.assertEqual((findings, skipped), ([], []))

    def test_five_page_object_stream_pdf_warns(self):
        findings, skipped = self.lint_pdf((PAGES / "objstm-5-pages.pdf").read_bytes())
        self.assertEqual(skipped, [])
        self.assertEqual([(item["level"], item["excerpt"]) for item in findings], [("warning", "5 pages")])

    def test_regex_fallback_when_document_parser_fails(self):
        pdf = b"%PDF-1.4\n" + b"".join(f"{i} 0 obj << /Type /Page >> endobj\n".encode() for i in range(1, 6)) + b"%%EOF"
        self.assertEqual(pdf_page_count(pdf), 5)
        findings, skipped = self.lint_pdf(pdf)
        self.assertEqual(skipped, [])
        self.assertEqual([item["excerpt"] for item in findings], ["5 pages"])

    def test_both_methods_failing_keeps_skip_message(self):
        for pdf in (b"", b"%PDF-1.4\nnot a pdf\n%%EOF", (PAGES / "objstm-3-pages.pdf").read_bytes()[:2000]):
            with self.subTest(size=len(pdf)):
                self.assertEqual(pdf_page_count(pdf), 0)
                self.assertEqual(self.lint_pdf(pdf), ([], [{"rule": "pdf-length", "reason": SKIP}]))


if __name__ == "__main__":
    unittest.main()
