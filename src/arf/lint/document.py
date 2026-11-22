"""Readers for the factory's numbered report format."""

from html import unescape
from html.parser import HTMLParser
import re


def plain(text):
    return unescape(re.sub(r"[*_`]", "", text)).replace("\u200e", "").replace("\u200f", "")


class HTMLDocument(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0
        self.media = []
        self.code = []
        self.footer_depth = 0
        self.footer = []
        self.line_numbers = [None]
        self.header_columns = 0

    def emit(self, text):
        self.parts.append(text)
        for index, chunk in enumerate(text.splitlines(keepends=True)):
            if chunk.strip() and self.line_numbers[-1] is None:
                self.line_numbers[-1] = self.getpos()[0] + index
            if chunk.endswith("\n"):
                self.line_numbers.append(None)

    def source_line(self, line):
        if 1 <= line <= len(self.line_numbers):
            return self.line_numbers[line - 1] or 1
        return 1

    def handle_starttag(self, tag, attrs):
        if tag == "footer":
            self.footer_depth += 1
        if tag == "thead":
            self.header_columns = 0
        if tag == "th":
            self.header_columns += 1
        attrs = {key: value or "" for key, value in attrs}
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        if tag in {"img", "script", "iframe", "beacon"}:
            self.media.append((self.getpos()[0], tag, attrs, self.get_starttag_text()))
        if tag in {"code", "pre"}:
            self.code.append((self.getpos()[0], self.get_starttag_text()))
        if not self.hidden:
            if re.fullmatch(r"h[1-6]", tag):
                self.emit("\n\n" + "#" * int(tag[1]) + " ")
            elif tag in {"p", "div", "footer", "ul", "ol"}:
                self.emit("\n\n")
            elif tag == "tr":
                self.emit("\n")
            elif tag == "br":
                self.emit("\n")
            elif tag == "li":
                self.emit("\n- ")
            elif tag in {"td", "th"}:
                self.emit(" | ")

    def handle_endtag(self, tag):
        if tag == "footer":
            self.footer_depth = max(0, self.footer_depth - 1)
        if tag in {"script", "style", "head"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden:
            if tag == "tr":
                self.emit(" |")
            elif tag == "thead" and self.header_columns:
                self.emit("\n|" + "---|" * self.header_columns)
            elif tag in {"p", "div", "footer", "table"} or re.fullmatch(r"h[1-6]", tag):
                self.emit("\n\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in {"script", "style", "head"}:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.hidden and (data.strip() or "\n" not in data):
            self.emit(data)
            if self.footer_depth:
                self.footer.append(data)

    @property
    def text(self):
        return "".join(self.parts)


def headings(text):
    return [(m.start(), len(m[1]), plain(m[2]).strip()) for m in
            re.finditer(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", text, re.M)]


def sections(text):
    result = []
    titles = [(offset, title) for offset, level, title in headings(text) if level <= 2]
    for index, (offset, title) in enumerate(titles):
        end = titles[index + 1][0] if index + 1 < len(titles) else len(text)
        result.append((offset, title, text[offset:end]))
    return result


def cells(line):
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def tables(text):
    lines = text.splitlines()
    result = []
    for index in range(1, len(lines)):
        if "|" not in lines[index - 1]:
            continue
        if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells(lines[index])):
            continue
        rows = []
        for number in range(index + 1, len(lines)):
            if "|" not in lines[number] or not lines[number].strip():
                break
            rows.append((number + 1, cells(lines[number]), lines[number]))
        result.append((cells(lines[index - 1]), rows))
    return result
