"""Indexed visible-text skeletons for hard phrase and money checks."""

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import re
import unicodedata

from .confusables import CONFUSABLES


class Visible(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source, self.chars, self.spans = source, [], []
        self.lines = [0] + [m.end() for m in re.finditer("\n", source)]
        self.tags = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = "hidden" in attrs or bool(re.search(
            r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important\s*)?(?:;|$)",
            attrs.get("style", ""), re.I))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.tags.append((tag, hidden or any(hidden for _, hidden in self.tags)))

    def handle_endtag(self, tag):
        for index in range(len(self.tags) - 1, -1, -1):
            if self.tags[index][0] == tag:
                del self.tags[index:]
                break

    def handle_data(self, data):
        if any(hidden for _, hidden in self.tags) or (
                any(tag in {"sup", "sub"} for tag, _ in self.tags) and data.strip().isdecimal()):
            return
        line, column = self.getpos()
        offset = self.lines[line - 1] + column
        self.chars.extend(data)
        self.spans.extend((offset + i, offset + i + 1) for i in range(len(data)))

    def entity(self, token):
        if any(hidden for _, hidden in self.tags):
            return
        line, column = self.getpos()
        start = self.lines[line - 1] + column
        end = start + len(token)
        if self.source[end:end + 1] == ";":
            end += 1
        value = unescape(self.source[start:end])
        self.chars.extend(value)
        self.spans.extend([(start, end)] * len(value))

    def handle_entityref(self, name):
        self.entity("&" + name)

    def handle_charref(self, name):
        self.entity("&#" + name)


def visible(source):
    masked = list(source)
    for match in re.finditer(r"\[[^\]\n]*\](\([^\n)]*\))", source):
        masked[match.start(1):match.end(1)] = " " * len(match[1])
    for match in re.finditer(r"\[[^\]\n]*\](\[[^\]\n]*\])|(?P<footnote>\[\^[^\]\n]+\])", source):
        start, end = match.span("footnote") if match["footnote"] else match.span(1)
        masked[start:end] = " " * (end - start)
    text = "".join(masked)
    if "<" not in text and "&" not in text:
        return text, [(i, i + 1) for i in range(len(text))]
    reader = Visible(text)
    reader.feed(text)
    if reader.rawdata:
        reader.handle_data(reader.rawdata)
    return "".join(reader.chars), reader.spans


def letter_or_number(char):
    return unicodedata.category(char)[0] in "LN"


def units(text, spans):
    index = 0
    while index < len(text):
        end = index + 1
        while end < len(text) and unicodedata.category(text[end]).startswith("M"):
            end += 1
        normalized = unicodedata.normalize("NFKC", text[index:end]).casefold()
        for char in normalized:
            if not letter_or_number(char):
                if char in "₪$€£¥₹":
                    yield char, (spans[index][0], spans[end - 1][1])
                continue
            if char.isdecimal():
                value = str(unicodedata.decimal(char))
            else:
                decomposed = "".join(c for c in unicodedata.normalize("NFKD", char)
                                     if not unicodedata.category(c).startswith("M"))
                value = "".join(CONFUSABLES.get(c, c) for c in decomposed)
            for base in value:
                if letter_or_number(base):
                    yield base, (spans[index][0], spans[end - 1][1])
        index = end


@dataclass(frozen=True)
class Match:
    source: str
    first: int
    last: int

    def start(self):
        return self.first

    def end(self):
        return self.last

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.source[self.first:self.last]


class Skeleton:
    def __init__(self, source, *, money=False):
        self.source = source
        self.visible, self.visible_spans = visible(source)
        pairs = [(char, span) for char, span in units(self.visible, self.visible_spans)
                 if money or letter_or_number(char)]
        self.text = "".join(char for char, _ in pairs)
        self.spans = [span for _, span in pairs]

    def boundary(self, start, end):
        first, last = self.spans[start][0], self.spans[end - 1][1]
        if start and self.spans[start - 1][1] > first:
            return False
        if end < len(self.spans) and self.spans[end][0] < last:
            return False
        before = next((c for c, span in reversed(list(zip(self.visible, self.visible_spans)))
                       if span[1] <= first and unicodedata.category(c) not in {"Cf", "Mn", "Mc", "Me"}), " ")
        after = next((c for c, span in zip(self.visible, self.visible_spans)
                      if span[0] >= last and unicodedata.category(c) not in {"Cf", "Mn", "Mc", "Me"}), " ")
        return not letter_or_number(before) and not letter_or_number(after)

    def match(self, start, end):
        return Match(self.source, self.spans[start][0], self.spans[end - 1][1])


HEBREW_PREFIXES = "והבכלמש"
HEBREW_SUFFIXES = ("", "ים", "ות", "י", "ה", "ו", "ם", "ן", "ת", "יו", "יה", "יהם", "יהן",
                   "יכם", "יכן", "ינו", "נו", "ך", "כם", "כן")


def hebrew_pattern(phrase):
    prefix = "[" + re.escape(Skeleton(HEBREW_PREFIXES).text) + "]*"
    suffix = "(?:" + "|".join(sorted({re.escape(Skeleton(s).text) for s in HEBREW_SUFFIXES},
                                    key=lambda s: (-len(s), s))) + ")"
    words = []
    for index, word in enumerate(phrase.split()):
        if index and word.startswith("ה"):
            word = word[1:]
        forms = {word}
        if word.endswith("ת") and not word.endswith("ות"):
            forms.update((word[:-1] + "ה", word[:-1] + "ות"))
        root = "(?:" + "|".join(sorted({re.escape(Skeleton(w).text) for w in forms})) + ")"
        words.append(prefix + root + suffix)
    return re.compile("".join(words))


class PhraseMatcher:
    def __init__(self, phrases):
        self.phrases = tuple(phrases)
        self.keys = tuple(sorted({Skeleton(phrase).text for phrase in phrases if not re.search("[א-ת]", phrase)},
                                 key=lambda p: (-len(p), p)))
        self.hebrew = tuple({hebrew_pattern(phrase) for phrase in phrases if re.search("[א-ת]", phrase)})

    def finditer(self, source):
        skeleton, found = Skeleton(source), []
        for key in self.keys:
            start = skeleton.text.find(key)
            while start >= 0:
                end = start + len(key)
                if skeleton.boundary(start, end):
                    found.append(skeleton.match(start, end))
                start = skeleton.text.find(key, start + 1)
        for pattern in self.hebrew:
            cursor = 0
            while match := pattern.search(skeleton.text, cursor):
                start = match.start()
                for end in range(match.end(), start, -1):
                    if skeleton.boundary(start, end) and pattern.fullmatch(skeleton.text, start, end):
                        found.append(skeleton.match(start, end))
                        break
                cursor = start + 1
        cursor = -1
        for match in sorted(found, key=lambda m: (m.start(), -m.end())):
            if match.start() >= cursor:
                yield match
                cursor = match.end()


class MoneyMatcher:
    codes = "(?:" + "|".join(sorted((Skeleton(code).text for code in (
        "nis", "ils", "usd", "eur", "gbp", "jpy", "chf", "aud", "cad", "sgd", "aed", "hkd", "nzd", "brl",
        "שח", "שקל", "שקלים", "dollar", "dollars", "euro", "euros")), key=len, reverse=True)) + ")"
    currency = rf"(?:₪|(?:us|ca|hk|nz|au|sg|a|c|r)?\$|€|£|¥|₹|{codes})"
    pattern = re.compile(rf"\d+{currency}|{currency}\d+|(?:us|ca|hk|nz|au|sg|a|c|r)?\$|[₪€£¥₹]")
    code_pattern = re.compile(rf"\d+{codes}|{codes}\d+")

    def finditer(self, source):
        found = {}
        for skeleton, pattern in ((Skeleton(source, money=True), self.pattern),
                                  (Skeleton(source), self.code_pattern)):
            for match in pattern.finditer(skeleton.text):
                if skeleton.boundary(match.start(), match.end()):
                    mapped = skeleton.match(match.start(), match.end())
                    found[mapped.start(), mapped.end()] = mapped
        yield from sorted(found.values(), key=lambda m: (m.start(), m.end()))


PRICING_PHRASES = (
    "הצעת מחיר", "תמחור", "עלות השירות", "מחירון", "price list", "price lists", "pricing",
    "quote", "quotes", "quotation", "quotations", "price quote", "price quotes",
    "price quotation", "price quotations", "fee", "fees", "per hour", "hourly rate",
    "hourly rates", "discount", "discounts", "retainer", "retainers", "quoted", "discounted", "per hr",
)
ROLE_PHRASES = tuple(
    phrase + suffix for phrase in (
        "dev team manager", "development team manager", "engineering manager",
        "R&D manager", "R and D manager", "team lead", "team leader", "development manager", "tech lead",
    ) for suffix in ("", "s")
) + tuple(dict.fromkeys(
    phrase.replace("צוות", team).replace("פיתוח", development)
    for phrase in ("ראש צוות", "ראשי צוות", "ראש צוות פיתוח", "ראשי צוות פיתוח",
                   "מנהל צוות פיתוח", "מנהלי צוות פיתוח", "מנהל פיתוח", "מנהלי פיתוח")
    for team in ("צוות", "צות") for development in ("פיתוח", "פתוח")
))
