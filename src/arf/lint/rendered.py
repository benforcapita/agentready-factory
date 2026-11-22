"""Render-then-lint: phrase and money rules on T5's customer-visible output."""

import logging
import re
import warnings

from .document import HTMLDocument, headings

# The rules whose source matcher masks markup (link destinations, footnote markers,
# hidden HTML, numeric sup/sub). Every other rule already reads the unmasked text.
RENDERED_RULES = ("price-in-offer", "currency-outside-offer", "ben-role")
PDF_VIEW = "rendered:report.pdf"
HTML_VIEW = "rendered:report.html"


# Attribute text a reader sees (tooltip, image fallback, form text) or assistive tech reads.
READER_ATTRIBUTES = ("title", "alt", "aria-label", "aria-description", "aria-roledescription",
                     "aria-placeholder", "aria-valuetext", "placeholder")
FORM_ATTRIBUTES = {"input": ("value",), "button": ("value",), "option": ("value", "label"),
                   "optgroup": ("label",), "track": ("label",)}


class DeliveredHTML(HTMLDocument):
    """Every text node, comment and reader-visible attribute of the HTML body, incl. CSS-hidden subtrees."""

    def __init__(self):
        super().__init__()
        self.attribute_text = []

    def emit(self, text):
        if self.attribute_text and text.startswith("\n\n"):
            pending, self.attribute_text = self.attribute_text, []
            super().emit("\n" + "\n".join(pending))
        super().emit(text)

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if self.hidden:
            return
        names = READER_ATTRIBUTES + FORM_ATTRIBUTES.get(tag, ())
        for name, value in attrs:
            # Attribute text joins the end of its block, so the section and proposal logic still apply.
            value = re.sub(r"^[\s#]+", "", re.sub(r"\s+", " ", value or "")).strip()
            if name in names and value:
                self.attribute_text.append(value)

    def handle_comment(self, data):
        if not self.hidden:
            self.emit("\n\n" + data + "\n\n")

    def close(self):
        super().close()
        self.emit("\n\n")


def html_text(document):
    reader = DeliveredHTML()
    reader.feed(document)
    reader.close()
    return reader.text


def pdf_text(rendered):
    """Text WeasyPrint lays out on the PDF pages, in logical order, with Markdown-style headings."""
    from weasyprint.formatting_structure import boxes

    parts = []

    def walk(box):
        if isinstance(box, boxes.TextBox):
            if box.style["visibility"] == "visible":
                parts.append(box.text)
            return
        tag = getattr(box, "element_tag", "") or ""
        block = isinstance(box, (boxes.BlockBox, boxes.TableRowBox))
        if block:
            parts.append("\n\n")
            if heading := re.fullmatch(r"h([1-6])", tag):
                parts.append("#" * int(heading[1]) + " ")
        elif isinstance(box, boxes.TableCellBox):
            parts.append(" | ")
        for child in getattr(box, "children", ()):
            walk(child)
        if isinstance(box, boxes.LineBox):
            parts.append("\n")
        elif block:
            parts.append("\n\n")

    for page in rendered.pages:
        walk(page._page_box)
    return re.sub(r"\n{3,}", "\n\n", "".join(parts))


def render_views(markdown_source):
    """T5's render of report.md: (view name, customer-visible text) pairs."""
    from arf.render import render_html
    from arf.render.pdf import layout_pdf

    lang = "he" if re.search("[\u0590-\u05ff]", markdown_source) else "en"
    document = render_html(markdown_source, lang=lang, title="", draft_marker=False)
    logger = logging.getLogger("weasyprint")
    level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            rendered, _ = layout_pdf(document)
    finally:
        logger.setLevel(level)
    return [(PDF_VIEW, pdf_text(rendered)), (HTML_VIEW, html_text(document))]


def inspect_view(text):
    """(rule, line, excerpt) for RENDERED_RULES on one view, with the source lint's section logic."""
    from .rules import MONEY, PRICING, ROLE, normalize_for_matching

    text = normalize_for_matching(text)
    findings, section = [], ""
    for block in re.finditer(r"\S[^\n]*(?:\n(?!\s*\n)[^\n]*)*", text):
        raw = block[0]
        line = text.count("\n", 0, block.start()) + 1
        if found := headings(raw):
            section = found[-1][2]
        numbered = re.match(r"([1-5])\.", section)
        proposal = bool(numbered and numbered[1] in {"4", "5"}) or (
            bool(re.search(r"תקציר[\s-]+מנהלים|Executive[\s-]+summary", section, re.I)) and
            bool(re.search(r"(?:ההצעה|proposal|our[\s-]+offer)\s*:", raw, re.I)))
        for match in PRICING.finditer(raw, rendered=True):
            findings.append(("price-in-offer", line, match[0]))
        for match in MONEY.finditer(raw, rendered=True):
            findings.append(("price-in-offer" if proposal else "currency-outside-offer", line, match[0]))
        for match in ROLE.finditer(raw, rendered=True):
            findings.append(("ben-role", line, match[0]))
    return findings
