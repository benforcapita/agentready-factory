"""HTML to an A4 PDF with WeasyPrint, without network or file access."""

from pathlib import Path


class BlockedResource(ValueError):
    """The HTML references a resource other than an inline data: URI."""


def render_pdf(document: str, out: Path) -> None:
    """Write `document` (a full HTML string) to `out` as a PDF with embedded fonts.

    Only inline data: URIs are loaded; any other reference (remote images, tracking
    pixels, external fonts or stylesheets) is refused and no PDF is written.
    """
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    blocked = []

    class DataOnlyFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            if url.startswith("data:"):
                return super().fetch(url, headers)
            blocked.append(url)
            raise BlockedResource(f"external resource blocked: {url}")

    pdf = HTML(string=document, url_fetcher=DataOnlyFetcher(allowed_protocols={"data"})).write_pdf()
    if blocked:
        raise BlockedResource("external resources are not allowed: " + ", ".join(sorted(set(blocked))))
    Path(out).write_bytes(pdf)
