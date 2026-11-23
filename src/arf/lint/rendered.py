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
VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
                 "source", "track", "wbr"}


# Elements whose text the HTML view already splits into blocks, lines or cells.
BLOCK_ELEMENTS = {"address", "article", "aside", "blockquote", "body", "caption", "dd", "details", "dialog",
                  "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
                  "h4", "h5", "h6", "header", "html", "li", "main", "nav", "ol", "p", "pre", "section",
                  "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul"}
# Starts an inline element's own text in the HTML view; inspect_view lints it as a separate unit.
UNIT = "\x1e"


def first_attributes(attrs):
    """Browsers keep the first of duplicate attributes."""
    seen, result = set(), []
    for name, value in attrs:
        if name not in seen:
            seen.add(name)
            result.append((name, value))
    return result


def css_hidden(attrs):
    style = first_attributes(attrs)
    style = re.sub(r"/\*.*?(?:\*/|$)", " ", dict(style).get("style") or "", flags=re.S)
    style = re.sub(r"\s+", "", style.lower())
    return any(name == "hidden" for name, _ in attrs) or bool(
        re.search(r"display:none|visibility:(?:hidden|collapse)", style))


class DeliveredHTML(HTMLDocument):
    """Every text node, comment and reader-visible attribute of the HTML body, incl. CSS-hidden subtrees.

    Each inline element's own text is also emitted as its own unit, so text glued to a neighbour by
    hiding this view does not recognize (class rules, font-size:0, noscript) still keeps its boundaries.
    """

    def __init__(self):
        super().__init__()
        self.attribute_text = []
        self.unit_text = []
        self.open_elements = []
        self.head_state = "before"

    # Only the document's real <head> (before <body>) is non-text; browsers and WeasyPrint
    # ignore a <head> tag inside the body and keep its children as body content.
    def real_head(self, tag, start):
        if tag == "body" and start and self.head_state == "open":
            super().handle_endtag("head")
        if tag == "body" and start:
            self.head_state = "done"
        if tag != "head":
            return True
        if start and self.head_state == "before":
            self.head_state = "open"
            return True
        if not start and self.head_state == "open":
            self.head_state = "done"
            return True
        return False

    def emit(self, text):
        if (self.attribute_text or self.unit_text) and text.startswith("\n\n"):
            pending, self.attribute_text = self.attribute_text, []
            units, self.unit_text = self.unit_text, []
            super().emit("".join("\n" + value for value in pending) + "".join("\n" + UNIT + value for value in units))
        super().emit(text)

    def split_open_elements(self):
        for element in self.open_elements:
            element["inline"] = False

    def close_element(self, element):
        if element["hidden"]:
            self.emit("\n")
        elif element["inline"]:
            text = re.sub(r"\s+", " ", "".join(element["text"])).strip()
            if text:
                self.unit_text.append(text)

    def handle_starttag(self, tag, attrs):
        if not self.real_head(tag, start=True):
            return
        attrs = first_attributes(attrs)
        super().handle_starttag(tag, attrs)
        if self.hidden:
            return
        if tag in BLOCK_ELEMENTS:
            self.split_open_elements()
        # Hidden subtrees are linted as their own unit: a boundary keeps them off the adjacent words.
        hidden = tag == "template" or css_hidden(attrs)
        if hidden:
            self.emit("\n")
        if tag not in VOID_ELEMENTS:
            self.open_elements.append({"tag": tag, "hidden": hidden, "inline": tag not in BLOCK_ELEMENTS,
                                       "text": []})
        else:
            if tag == "br":
                self.split_open_elements()
            if hidden:
                self.emit("\n")
        names = READER_ATTRIBUTES + FORM_ATTRIBUTES.get(tag, ())
        for name, value in attrs:
            # Attribute text joins the end of its block, so the section and proposal logic still apply.
            value = re.sub(r"^[\s#]+", "", re.sub(r"\s+", " ", value or "")).strip()
            if name in names and value:
                self.attribute_text.append(value)

    def handle_endtag(self, tag):
        if not self.real_head(tag, start=False):
            return
        if any(element["tag"] == tag for element in self.open_elements):
            while self.open_elements:
                element = self.open_elements.pop()
                self.close_element(element)
                if element["tag"] == tag:
                    break
        if tag in BLOCK_ELEMENTS:
            self.split_open_elements()
        super().handle_endtag(tag)

    # Browsers ignore "/>" on HTML elements: <span hidden/> stays open and hides the text after it.
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        super().handle_data(data)
        if not self.hidden:
            for element in self.open_elements:
                element["text"].append(data)

    def handle_comment(self, data):
        if not self.hidden:
            self.emit("\n\n" + data + "\n\n")

    def close(self):
        super().close()
        while self.open_elements:
            self.close_element(self.open_elements.pop())
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
    from .matching import Skeleton
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
        main, *units = raw.split(UNIT)
        seen = set()
        for index, part in enumerate([main, *units]):
            found = [("price-in-offer", match[0]) for match in PRICING.finditer(part, rendered=True)]
            found += [("price-in-offer" if proposal else "currency-outside-offer", match[0])
                      for match in MONEY.finditer(part, rendered=True)]
            found += [("ben-role", match[0]) for match in ROLE.finditer(part, rendered=True)]
            for rule, excerpt in found:
                key = (rule, Skeleton(excerpt, rendered=True).text)
                if index and key in seen:
                    continue
                seen.add(key)
                findings.append((rule, line, excerpt))
    return findings
