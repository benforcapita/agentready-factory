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
    ".draft-marker{border-top:2px solid #b42318;color:#b42318;background:#fef3f2;font-weight:bold;"
    "text-align:center;padding:6px 10px}\n"
    "@media screen{body{padding-bottom:56px}.draft-marker{position:fixed;left:0;right:0;bottom:0}}\n"
    "@media print{.draft-marker{display:none}}\n"
    '@page{@bottom-center{content:"' + DRAFT_MARKER + '";font-family:"Assistant","Rubik","Heebo","Segoe UI",Arial,sans-serif;color:#b42318;font-weight:bold;font-size:11px}}\n'
)

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
_DRAFT_COMMENT = re.compile(r"<!--\s*arf:draft\s+run=(\S*?)\s*-->\n?")


def draft_comment(run_id: str) -> str:
    """The LLD 6.2 draft comment that marks a shadow/reviewed report.md or report.html."""
    if not _RUN_ID.fullmatch(run_id or ""):
        raise ValueError(f"invalid run id {run_id!r}; use letters, digits and . _ : -")
    return f"<!-- arf:draft run={run_id} -->"


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
    run_id: str | None = None,
) -> str:
    """Render report Markdown to a standalone HTML document with no external resources.

    Drafts (the default) carry the LLD 6.2 marker: the comment `<!-- arf:draft run=<id> -->`
    and the footer line DRAFT_MARKER on screen and on every PDF page. The run id comes from
    `run_id` or from the comment already in report.md. `draft_marker=False` is `--final`.
    """
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language {lang!r}; expected one of {', '.join(LANGUAGES)}")
    language = LANGUAGES[lang]

    source = source.replace("\u200e", "").replace("\u200f", "")
    source_runs = set(_DRAFT_COMMENT.findall(source))
    source = _DRAFT_COMMENT.sub("", source)
    comment = ""
    if draft_marker:
        if len(source_runs) > 1 or (run_id and source_runs and source_runs != {run_id}):
            runs = ", ".join(sorted(source_runs | ({run_id} if run_id else set())))
            raise ValueError(f"conflicting arf:draft run ids: {runs}")
        run_id = run_id or next(iter(source_runs), None)
        if not run_id:
            raise ValueError("a draft needs a run id: pass --run-id or keep <!-- arf:draft run=<id> --> in report.md")
        comment = draft_comment(run_id) + "\n"
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
        body = f'{comment}{body}\n<footer class="draft-marker" role="note">{DRAFT_MARKER}</footer>'
    if title is None:
        title = _first_heading(body)
    direction = language.direction
    return (
        f'<!DOCTYPE html>\n<html lang="{lang}" dir="{direction}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(title, quote=False)}</title>\n<style>{css}</style>\n</head>\n"
        f'<body dir="{direction}">\n{body}\n</body>\n</html>\n'
    )
