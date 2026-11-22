"""python -m arf.render: the entry point `arf render` will use."""

import argparse
from pathlib import Path
import sys

from .html import LANGUAGES, render_html
from .pdf import render_pdf


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arf render",
        description="Render report.md to report.html and an A4 report.pdf (drafts only)",
    )
    parser.add_argument("report", type=Path, help="Path to report.md")
    parser.add_argument("--out", type=Path, required=True, help="Output directory")
    parser.add_argument("--lang", choices=sorted(LANGUAGES), default="he", help="Report language (default: he)")
    parser.add_argument("--title", help="HTML/PDF title (default: the first H1)")
    parser.add_argument(
        "--run-id",
        help="Run id for <!-- arf:draft run=<id> --> (default: the one already in report.md)",
    )
    parser.add_argument(
        "--final",
        "--no-draft-marker",
        dest="draft_marker",
        action="store_false",
        help="Omit the draft marker; only Ben renders final (the factory never does)",
    )
    parser.add_argument("--no-pdf", dest="pdf", action="store_false", help="Write report.html only")
    args = parser.parse_args(argv)

    try:
        source = args.report.read_text(encoding="utf-8")
        document = render_html(
            source, lang=args.lang, title=args.title, draft_marker=args.draft_marker, run_id=args.run_id
        )
        args.out.mkdir(parents=True, exist_ok=True)
        html_path = args.out / "report.html"
        html_path.write_text(document, encoding="utf-8")
        print(f"Wrote {html_path}")
        if args.pdf:
            pdf_path = args.out / "report.pdf"
            render_pdf(document, pdf_path)
            print(f"Wrote {pdf_path}")
    except Exception as error:
        print(f"arf render: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
