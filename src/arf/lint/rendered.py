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


HIDING = re.compile(r"(?:^|[;{])(?:display:none|visibility:(?:hidden|collapse)|color:transparent"
                    r"|(?:font-size|opacity):(?:0+(?:\.0*)?|\.0+)(?:[a-z]+|%)?(?=$|[;!}]))")
HIDDEN_ELEMENTS = {"template", "noscript"}


def first_attributes(attrs):
    """Browsers keep the first of duplicate attributes."""
    seen, result = set(), []
    for name, value in attrs:
        if name not in seen:
            seen.add(name)
            result.append((name, value))
    return result


def declarations(css):
    return re.sub(r"\s+", "", re.sub(r"/\*.*?(?:\*/|$)", " ", css, flags=re.S).lower())


def hiding_selectors(css):
    """(tag, classes, id) subjects of <style> rules that hide text on screen (not only in print)."""
    subjects, stack, buffer = [], [], ""
    for char in re.sub(r"/\*.*?(?:\*/|$)", " ", css, flags=re.S):
        if char == "{":
            stack.append(buffer.strip().lower())
            buffer = ""
        elif char == "}" and stack:
            prelude = stack.pop()
            print_only = any(re.match(r"@media\b(?!.*\b(?:screen|all)\b).*\bprint\b", outer)
                             for outer in stack)
            if not prelude.startswith("@") and not print_only and HIDING.search(declarations(buffer)):
                for selector in prelude.split(","):
                    subject = re.split(r"[\s>+~]+", selector.strip())[-1]
                    if match := re.fullmatch(r"([a-z][a-z0-9-]*|\*)?((?:[.#][\w-]+)*)", subject):
                        parts = re.findall(r"([.#])([\w-]+)", match[2])
                        subjects.append((match[1] or "*", {name for kind, name in parts if kind == "."},
                                         {name for kind, name in parts if kind == "#"}))
            buffer = ""
        else:
            buffer += char
    return subjects


def css_hidden(attrs, tag="", selectors=()):
    attrs = dict(attrs)
    classes, ids = set((attrs.get("class") or "").split()), {attrs.get("id") or ""}
    return "hidden" in attrs or tag in HIDDEN_ELEMENTS or bool(HIDING.search(declarations(attrs.get("style") or ""))) or any(
        rule_tag in {"*", tag} and rule_classes <= classes and rule_ids <= ids
        for rule_tag, rule_classes, rule_ids in selectors)


class DeliveredHTML(HTMLDocument):
    """Every text node, comment and reader-visible attribute of the HTML body, incl. CSS-hidden subtrees.

    A hidden subtree is set off from the visible words around it, so text glued to it keeps its boundaries.
    """

    def __init__(self):
        super().__init__()
        self.attribute_text = []
        self.open_elements = []
        self.hiding_selectors = []
        self.style_text = None
        self.boundary = False
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
        # A boundary closing a hidden subtree is written lazily, so adjacent hidden subtrees join up.
        if self.boundary:
            self.boundary = False
            self.emit("\n")
        if self.attribute_text and text.startswith("\n\n"):
            pending, self.attribute_text = self.attribute_text, []
            super().emit("".join("\n" + value for value in pending))
        super().emit(text)

    def inside_hidden(self):
        return any(element["hidden"] for element in self.open_elements)

    def handle_starttag(self, tag, attrs):
        if not self.real_head(tag, start=True):
            return
        attrs = first_attributes(attrs)
        if tag == "style":
            self.style_text = []
        super().handle_starttag(tag, attrs)
        if self.hidden:
            return
        # Hidden subtrees are linted as their own unit: a boundary keeps them off the adjacent words.
        hidden = css_hidden(attrs, tag, self.hiding_selectors) and not self.inside_hidden()
        if hidden:
            if self.boundary:
                self.boundary = False
            else:
                self.emit("\n")
        if tag not in VOID_ELEMENTS:
            self.open_elements.append({"tag": tag, "hidden": hidden})
        elif hidden:
            self.boundary = True
        names = READER_ATTRIBUTES + FORM_ATTRIBUTES.get(tag, ())
        for name, value in attrs:
            # Attribute text joins the end of its block, so the section and proposal logic still apply.
            value = re.sub(r"^[\s#]+", "", re.sub(r"\s+", " ", value or "")).strip()
            if name in names and value:
                self.attribute_text.append(value)

    def handle_endtag(self, tag):
        if not self.real_head(tag, start=False):
            return
        if tag == "style" and self.style_text is not None:
            self.hiding_selectors += hiding_selectors("".join(self.style_text))
            self.style_text = None
        if any(element["tag"] == tag for element in self.open_elements):
            while self.open_elements:
                element = self.open_elements.pop()
                if element["hidden"]:
                    self.boundary = True
                if element["tag"] == tag:
                    break
        super().handle_endtag(tag)

    # Browsers ignore "/>" on HTML elements: <span hidden/> stays open and hides the text after it.
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag == "style":
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.style_text is not None:
            self.style_text.append(data)
        super().handle_data(data)

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
