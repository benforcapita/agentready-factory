"""HTML to an A4 PDF with WeasyPrint, without network or file access."""

from pathlib import Path


class BlockedResource(ValueError):
    """The HTML references a resource other than an inline data: URI."""


def layout_pdf(document: str):
    """Lay out `document` exactly as render_pdf does, without writing a file.

    Returns the WeasyPrint document and the sorted URLs that were refused.
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

    rendered = HTML(string=document, url_fetcher=DataOnlyFetcher(allowed_protocols={"data"})).render()
    return rendered, sorted(set(blocked))


def render_pdf(document: str, out: Path) -> int:
    """Write `document` (a full HTML string) to `out` as a PDF with embedded fonts.

    Only inline data: URIs are loaded; any other reference (remote images, tracking
    pixels, external fonts or stylesheets) is refused and no PDF is written.
    """
    rendered, blocked = layout_pdf(document)
    pdf = rendered.write_pdf()
    if blocked:
        raise BlockedResource("external resources are not allowed: " + ", ".join(blocked))
    Path(out).write_bytes(pdf)
    return len(rendered.pages)
