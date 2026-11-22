"""Markdown to HTML, generalized from the Lehamim build_html.py."""

from dataclasses import dataclass
import html
import re

import markdown


DRAFT_MARKER = "טיוטה — לא לשליחה · DRAFT"

_BASE_CSS = """
@page{size:A4;margin:16mm 14mm}
body{font-family:"Assistant","Rubik","Heebo","Segoe UI",Arial,sans-serif;line-height:1.6;color:#1b1f24;max-width:900px;margin:24px auto;padding:0 20px;font-size:15px}
h1{font-size:26px;color:#0b3d91;border-bottom:3px solid #0b3d91;padding-bottom:8px;margin-bottom:12px}
h2{font-size:20px;color:#0b3d91;margin-top:30px;border-bottom:1px solid #d0d7de;padding-bottom:4px}
h3{font-size:16px;color:#1f4f9c;margin-top:18px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px;page-break-inside:avoid}
th,td{border:1px solid #c9d1d9;padding:6px 8px;text-align:ALIGN;vertical-align:top}
th{background:#eef3fb}
tr:nth-child(even) td{background:#fafbfc}
hr{border:none;border-top:1px solid #d0d7de;margin:22px 0}
.en{unicode-bidi:isolate;direction:ltr;font-family:"Segoe UI",Arial,sans-serif}
strong{color:#0d1b2a}
.summary{background:#f3f7fd;border:1px solid #b9cbe8;border-radius:8px;padding:6px 18px 12px;page-break-after:auto}
@media print{body{margin:0;max-width:none;font-size:12.5px}a{color:inherit;text-decoration:none}h2{page-break-after:avoid}}
"""

_MARKER_CSS = (
    ".draft-marker{border:2px solid #b42318;color:#b42318;background:#fef3f2;font-weight:bold;"
    "text-align:center;padding:6px 10px;margin:0 0 16px;border-radius:6px}\n"
    '@page{@top-center{content:"' + DRAFT_MARKER + '";font-family:"Assistant","Rubik","Heebo","Segoe UI",Arial,sans-serif;color:#b42318;font-weight:bold;font-size:11px}}\n'
)


@dataclass(frozen=True)
class Language:
    direction: str
    align: str
    summary_heading: str
    wrap_latin: bool


LANGUAGES = {
    "he": Language("rtl", "right", "תקציר מנהלים", True),
    "en": Language("ltr", "left", "Executive summary", False),
}

_TOKEN = r"[A-Za-z0-9./@:_+\-~'\"]*[A-Za-z][A-Za-z0-9./@:_+\-~]*"
_LATIN_RUN = re.compile(r"(?:/?" + _TOKEN + r")(?:[ ,/&]+(?:/?" + _TOKEN + r"))*|/|0\d{2}-\d{3}-\d{4}")
_PLACEHOLDER = re.compile(r"[A-Z][A-Z0-9_]*")
_TAG = re.compile(r"(<[^>]+>)")


def _wrap_latin_runs(text: str) -> str:
    def wrap(match: re.Match) -> str:
        run = match.group(0)
        if run == "/":
            return run
        # Sanitized placeholders such as [CONTACT_NAME] stand in for redacted values
        # whose script is unknown, so they keep the paragraph direction.
        start, end = match.span()
        if (
            text[start - 1 : start] == "["
            and text[end : end + 1] == "]"
            and _PLACEHOLDER.fullmatch(run)
        ):
            return run
        tail = ""
        while run and run[-1] in ".,:;":
            tail = run[-1] + tail
            run = run[:-1]
        while run and run[-1] in " ,":
            tail = run[-1] + tail
            run = run[:-1]
        if not run:
            return match.group(0)
        return f'<span dir="ltr" class="en">{run}</span>{tail}'

    return _LATIN_RUN.sub(wrap, text)


def _first_heading(body: str) -> str:
    match = re.search(r"<h1>(.*?)</h1>", body, flags=re.S)
    if not match:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()


def render_html(
    source: str,
    *,
    lang: str = "he",
    title: str | None = None,
    draft_marker: bool = True,
) -> str:
    """Render report Markdown to a standalone HTML document with no external resources."""
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language {lang!r}; expected one of {', '.join(LANGUAGES)}")
    language = LANGUAGES[lang]

    source = source.replace("\u200e", "").replace("\u200f", "")
    body = markdown.markdown(source, extensions=["tables", "nl2br"])
    if language.wrap_latin:
        body = "".join(part if part.startswith("<") else _wrap_latin_runs(part) for part in _TAG.split(body))
    body = body.replace("<table>", f'<table dir="{language.direction}">')
    body = re.sub(
        r"(<h2>" + re.escape(language.summary_heading) + r".*?)(<hr />)",
        r'<section class="summary">\1</section>\n\2',
        body,
        count=1,
        flags=re.S,
    )

    css = _BASE_CSS.replace("ALIGN", language.align)
    if draft_marker:
        css += _MARKER_CSS
        body = f'<div class="draft-marker" role="note">{DRAFT_MARKER}</div>\n{body}'
    if title is None:
        title = _first_heading(body)
    direction = language.direction
    return (
        f'<!DOCTYPE html>\n<html lang="{lang}" dir="{direction}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(title, quote=False)}</title>\n<style>{css}</style>\n</head>\n"
        f'<body dir="{direction}">\n{body}\n</body>\n</html>\n'
    )
