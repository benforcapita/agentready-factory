"""Conservative marker verification without a PDF text-extraction dependency."""

import re
import zlib

from .markers import DRAFT_MARKER

MAX_DECODED_STREAM = 8 * 1024 * 1024


def has_draft_marker(pdf):
    markers = tuple(DRAFT_MARKER.encode(encoding) for encoding in ("utf-8", "utf-16-be", "utf-16-le"))
    if any(marker in pdf for marker in markers):
        return True
    for match in re.finditer(rb"<<(.*?)>>\s*stream(?:\r\n|\n|\r)(.*?)\s*endstream", pdf, re.S):
        if not re.search(rb"/FlateDecode\b", match[1]):
            continue
        try:
            decoded = zlib.decompressobj().decompress(match[2], MAX_DECODED_STREAM)
        except zlib.error:
            continue
        if any(marker in decoded for marker in markers):
            return True
    return False
