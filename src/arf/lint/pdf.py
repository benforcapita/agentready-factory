"""Bounded page-text extraction for unencrypted ToUnicode PDFs."""

from dataclasses import dataclass
import re
import zlib

from .markers import DRAFT_MARKER

MAX_BYTES = 8 * 1024 * 1024
MAX_OBJECTS = 10000
SPACE = b"\x00\t\n\x0c\r "


class PDFError(ValueError):
    pass


class Name(str):
    pass


@dataclass(frozen=True)
class Ref:
    number: int
    generation: int


@dataclass
class Stream:
    dictionary: dict
    data: bytes

    def decoded(self):
        filters = self.dictionary.get("Filter", [])
        if isinstance(filters, Name):
            filters = [filters]
        if not filters:
            return self.data
        if filters != [Name("FlateDecode")] or self.dictionary.get("DecodeParms") is not None:
            raise PDFError("unsupported stream filter")
        decoder = zlib.decompressobj()
        result = decoder.decompress(self.data, MAX_BYTES + 1)
        if len(result) > MAX_BYTES or not decoder.eof:
            raise PDFError("unreadable or oversized stream")
        return result


class Reader:
    def __init__(self, data, position=0):
        self.data, self.position = data, position

    def skip(self):
        while self.position < len(self.data):
            if self.data[self.position] in SPACE:
                self.position += 1
            elif self.data[self.position:self.position + 1] == b"%":
                end = re.search(rb"[\r\n]", self.data[self.position:])
                self.position = self.position + end.end() if end else len(self.data)
            else:
                break

    def token(self):
        self.skip()
        start = self.position
        if start >= len(self.data):
            raise PDFError("unexpected end of PDF")
        if self.data[start:start + 2] in {b"<<", b">>"}:
            self.position += 2
            return self.data[start:self.position].decode()
        char = self.data[start:start + 1]
        self.position += 1
        if char in {b"[", b"]"}:
            return char.decode()
        if char == b"(":
            result, depth = bytearray(), 1
            while self.position < len(self.data):
                byte = self.data[self.position]
                self.position += 1
                if byte == 92:
                    if self.position == len(self.data):
                        break
                    byte = self.data[self.position]
                    self.position += 1
                    if byte in b"01234567":
                        octal = bytes([byte])
                        for _ in range(2):
                            if self.position < len(self.data) and self.data[self.position] in b"01234567":
                                octal += self.data[self.position:self.position + 1]
                                self.position += 1
                        result.append(int(octal, 8) % 256)
                    elif byte not in b"\r\n":
                        result.append({110: 10, 114: 13, 116: 9, 98: 8, 102: 12}.get(byte, byte))
                    elif byte == 13 and self.data[self.position:self.position + 1] == b"\n":
                        self.position += 1
                else:
                    depth += (byte == 40) - (byte == 41)
                    if depth == 0:
                        return bytes(result)
                    if depth > 64:
                        raise PDFError("nested PDF string")
                    result.append(byte)
            raise PDFError("unterminated string")
        if char == b"<":
            end = self.data.find(b">", self.position)
            if end < 0:
                raise PDFError("unterminated hex string")
            value = re.sub(rb"\s", b"", self.data[self.position:end])
            self.position = end + 1
            return bytes.fromhex((value + (b"0" if len(value) % 2 else b"")).decode("ascii"))
        while self.position < len(self.data) and self.data[self.position] not in SPACE + b"()<>[]{}/%":
            self.position += 1
        raw = self.data[start:self.position]
        if char == b"/":
            return Name(re.sub(rb"#([0-9a-fA-F]{2})", lambda m: bytes([int(m[1], 16)]), raw[1:]).decode("latin1"))
        if re.fullmatch(rb"[+-]?\d+", raw):
            return int(raw)
        if re.fullmatch(rb"[+-]?(?:\d+\.\d*|\.\d+)", raw):
            return float(raw)
        return raw.decode("latin1")

    def value(self, depth=0):
        if depth > 64:
            raise PDFError("nested PDF objects")
        token = self.token()
        if token == "<<":
            result = {}
            while True:
                key = self.token()
                if key == ">>":
                    return result
                if not isinstance(key, Name) or key in result:
                    raise PDFError("invalid dictionary")
                result[key] = self.value(depth + 1)
        if token == "[":
            result = []
            while True:
                self.skip()
                if self.data[self.position:self.position + 1] == b"]":
                    self.position += 1
                    return result
                result.append(self.value(depth + 1))
        if type(token) is int:
            saved = self.position
            try:
                generation = self.token()
                if type(generation) is int and self.token() == "R":
                    return Ref(token, generation)
            except PDFError:
                pass
            self.position = saved
        return None if token == "null" else token


class Document:
    def __init__(self, data):
        if len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
            raise PDFError("unsupported PDF")
        self.objects, offsets, trailers = {}, {}, {}
        position, xref = 0, None
        while match := re.search(rb"(?m)(\d+)\s+(\d+)\s+obj\b|\btrailer\b|\bstartxref\b", data[position:]):
            start = position + match.start()
            position += match.end()
            reader = Reader(data, position)
            if match[1] is None:
                value = reader.value()
                if match[0] == b"trailer":
                    trailers[start] = value
                else:
                    xref = value
                position = reader.position
                continue
            reference = Ref(int(match[1]), int(match[2]))
            value = reader.value()
            reader.skip()
            if data[reader.position:reader.position + 6] == b"stream":
                if not isinstance(value, dict) or type(value.get("Length")) is not int:
                    raise PDFError("indirect stream length unsupported")
                begin = re.match(rb"stream(?:\r\n|\r|\n)", data[reader.position:])
                if not begin or value["Length"] < 0:
                    raise PDFError("invalid stream")
                reader.position += begin.end()
                end = reader.position + value["Length"]
                value = Stream(value, data[reader.position:end])
                reader.position = end
                if reader.token() != "endstream":
                    raise PDFError("invalid stream length")
            if reader.token() != "endobj":
                raise PDFError("invalid object")
            self.add(reference, value)
            offsets[start] = value
            position = reader.position
        if type(xref) is not int:
            raise PDFError("missing cross-reference")
        trailer = offsets.get(xref)
        if isinstance(trailer, Stream) and trailer.dictionary.get("Type") == "XRef":
            trailer = trailer.dictionary
        elif data[xref:xref + 4] == b"xref":
            trailer = next((trailers[key] for key in sorted(trailers) if key > xref), None)
        else:
            raise PDFError("unsupported cross-reference")
        if not isinstance(trailer, dict) or "Encrypt" in trailer or "Prev" in trailer:
            raise PDFError("encrypted/incremental PDF unsupported")
        for value in list(self.objects.values()):
            if not isinstance(value, Stream) or value.dictionary.get("Type") != "ObjStm":
                continue
            raw = value.decoded()
            count, first = value.dictionary.get("N"), value.dictionary.get("First")
            if type(count) is not int or type(first) is not int or not 0 <= count <= MAX_OBJECTS or not 0 <= first <= len(raw):
                raise PDFError("invalid object stream")
            header = Reader(raw[:first])
            for _ in range(count):
                number, offset = header.token(), header.token()
                if type(number) is not int or type(offset) is not int or not 0 <= offset < len(raw) - first:
                    raise PDFError("invalid object offset")
                self.add(Ref(number, 0), Reader(raw, first + offset).value())
        catalog = self.resolve(trailer.get("Root"))
        if not isinstance(catalog, dict) or catalog.get("Type") != "Catalog":
            raise PDFError("missing catalog")
        self.root = catalog.get("Pages")

    def add(self, reference, value):
        if reference in self.objects or len(self.objects) >= MAX_OBJECTS:
            raise PDFError("duplicate or excessive objects")
        self.objects[reference] = value

    def resolve(self, value):
        seen = set()
        while isinstance(value, Ref):
            if value in seen or value not in self.objects:
                raise PDFError("cyclic or missing reference")
            seen.add(value)
            value = self.objects[value]
        return value

    def pages(self):
        seen = set()
        extracted = decoded = 0

        def walk(reference, resources, depth):
            nonlocal extracted, decoded
            if depth > 64 or not isinstance(reference, Ref) or reference in seen:
                raise PDFError("invalid page tree")
            seen.add(reference)
            page = self.resolve(reference)
            if not isinstance(page, dict):
                raise PDFError("invalid page")
            resources = self.resolve(page.get("Resources", resources))
            if page.get("Type") == "Pages":
                kids = self.resolve(page.get("Kids"))
                if not isinstance(kids, list):
                    raise PDFError("invalid page children")
                for child in kids:
                    yield from walk(child, resources, depth + 1)
            elif page.get("Type") == "Page":
                contents = self.resolve(page.get("Contents", []))
                if not isinstance(contents, list):
                    contents = [contents]
                chunks = [self.resolve(content) for content in contents]
                if any(not isinstance(content, Stream) for content in chunks) or not isinstance(resources, dict):
                    raise PDFError("invalid page content/resources")
                decoded_chunks = []
                for content in chunks:
                    chunk = content.decoded()
                    decoded += len(chunk) + 1
                    if decoded > MAX_BYTES:
                        raise PDFError("excessive page content")
                    decoded_chunks.append(chunk)
                raw = b"\n".join(decoded_chunks)
                text = self.page_text(raw, resources)
                extracted += len(text)
                if extracted > MAX_BYTES:
                    raise PDFError("excessive extracted text")
                yield text
            else:
                raise PDFError("invalid page type")

        return walk(self.root, {}, 0)

    def page_text(self, raw, resources):
        reader, operands, result, font, in_text = Reader(raw), [], [], None, False
        fonts, maps, stack = self.resolve(resources.get("Font", {})), {}, []
        characters = 0
        while True:
            reader.skip()
            if reader.position == len(raw):
                break
            value = reader.value()
            if type(value) is not str:
                operands.append(value)
                continue
            if value == "q":
                if len(stack) >= 64:
                    raise PDFError("nested graphics state")
                stack.append(font)
            elif value == "Q":
                if not stack:
                    raise PDFError("unbalanced graphics state")
                font = stack.pop()
            elif value == "BT":
                if in_text:
                    raise PDFError("nested text object")
                in_text = True
                result.append("\n")
            elif value == "ET":
                if not in_text:
                    raise PDFError("unbalanced text object")
                in_text = False
                result.append("\n")
            elif value in {"Tm", "Td", "TD", "T*"}:
                result.append("\n")
            elif value == "Tf":
                if len(operands) != 2 or not isinstance(operands[0], Name):
                    raise PDFError("invalid text font")
                font = operands[0]
            elif value in {"Tj", "TJ", "'", '"'}:
                if not in_text or font not in fonts or not operands:
                    raise PDFError("text without font")
                if font not in maps:
                    descriptor = self.resolve(fonts[font])
                    cmap = self.resolve(descriptor.get("ToUnicode")) if isinstance(descriptor, dict) else None
                    if not isinstance(cmap, Stream):
                        raise PDFError("font lacks ToUnicode")
                    maps[font] = unicode_map(cmap.decoded())
                if value in {"'", '"'}:
                    result.append("\n")
                parts = operands[-1] if value == "TJ" else [operands[-1]]
                if not isinstance(parts, list):
                    raise PDFError("invalid text array")
                for part in parts:
                    if isinstance(part, bytes):
                        text = decode_text(part, maps[font])
                        characters += len(text)
                        if characters > MAX_BYTES:
                            raise PDFError("excessive text")
                        result.append(text)
                    elif type(part) not in {int, float}:
                        raise PDFError("invalid text operand")
            elif value in {"BI", "Do", "Tr", "gs"}:
                if value == "Tr" and operands == [0]:
                    pass
                elif value == "gs" and len(operands) == 1:
                    state = self.resolve(self.resolve(resources.get("ExtGState", {})).get(operands[0]))
                    if not isinstance(state, dict) or set(state) - {"Type", "ca", "CA"} or state.get("ca", 1) != 1 or state.get("CA", 1) != 1:
                        raise PDFError("unsupported graphics state")
                else:
                    raise PDFError("unsupported image/form/invisible text")
            operands = []
        if in_text or stack or operands:
            raise PDFError("unterminated page content")
        return "".join(result)


def unicode_map(raw):
    result = {}
    for match in re.finditer(rb"(\d+)\s+beginbf(char|range)\b(.*?)\bendbf(?:char|range)\b", raw, re.S):
        count, reader = int(match[1]), Reader(match[3])
        if count > 65536:
            raise PDFError("excessive CMap")
        for _ in range(count):
            start = reader.value()
            if not isinstance(start, bytes) or not 1 <= len(start) <= 4:
                raise PDFError("invalid character code")
            end = start if match[2] == b"char" else reader.value()
            target = reader.value()
            if not isinstance(end, bytes) or len(end) != len(start):
                raise PDFError("invalid character range")
            low, high = int.from_bytes(start), int.from_bytes(end)
            if high < low or high - low > 65536 or len(result) + high - low + 1 > 65536:
                raise PDFError("excessive character range")
            if not isinstance(target, (bytes, list)) or isinstance(target, list) and len(target) != high - low + 1:
                raise PDFError("invalid Unicode range")
            for offset in range(high - low + 1):
                destination = target[offset] if isinstance(target, list) else (int.from_bytes(target) + offset).to_bytes(len(target))
                if not isinstance(destination, bytes) or not 0 < len(destination) <= 32 or len(destination) % 2:
                    raise PDFError("invalid Unicode mapping")
                result[(low + offset).to_bytes(len(start))] = destination.decode("utf-16-be")
    if not result:
        raise PDFError("unsupported CMap")
    return result


def decode_text(raw, mapping):
    result, position, characters = [], 0, 0
    widths = sorted({len(code) for code in mapping}, reverse=True)
    while position < len(raw):
        code = next((raw[position:position + width] for width in widths if raw[position:position + width] in mapping), None)
        if code is None:
            raise PDFError("unmapped character")
        characters += len(mapping[code])
        if characters > MAX_BYTES:
            raise PDFError("excessive decoded text")
        result.append(mapping[code])
        position += len(code)
    return "".join(result)


def marker_forms():
    """DRAFT_MARKER in logical order and in the visual glyph order of an RTL and an LTR line."""
    rtl = re.sub(r"[A-Za-z]+", lambda match: match[0][::-1], DRAFT_MARKER[::-1])
    ltr = re.sub(r"[\u0590-\u05ff](?:[^A-Za-z\u0590-\u05ff]*[\u0590-\u05ff])*", lambda match: match[0][::-1], DRAFT_MARKER)
    return {DRAFT_MARKER, rtl, ltr}


def has_draft_marker(pdf):
    forms = marker_forms()
    try:
        pages = list(Document(pdf).pages())
        return bool(pages) and all(any(" ".join(line.split()) in forms
                                       for line in page.splitlines()) for page in pages)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, zlib.error):
        return False
