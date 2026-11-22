"""JSON-LD extraction from rendered HTML."""

from html.parser import HTMLParser
import json

JSONLD_MEDIA_TYPE = "application/ld+json"
PAGE_TYPES = ("WebPage", "CollectionPage", "ItemPage", "AboutPage", "ContactPage", "ProfilePage")


class _ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            media = (dict(attrs).get("type") or "").split(";")[0].strip().lower()
            self._current = [] if media == JSONLD_MEDIA_TYPE else None

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._current is not None:
            self.blocks.append("".join(self._current))
            self._current = None


def _strip_wrappers(text):
    text = text.strip()
    for start, end in (("<!--", "-->"), ("<![CDATA[", "]]>")):
        if text.startswith(start) and text.endswith(end):
            text = text[len(start):-len(end)].strip()
    return text


def _types(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _walk(node, found):
    if isinstance(node, dict):
        found.update(_types(node.get("@type")))
        for value in node.values():
            _walk(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found)


def _top_nodes(data):
    nodes = data if isinstance(data, list) else [data]
    result = []
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("@graph"), list):
            result.extend(item for item in node["@graph"] if isinstance(item, dict))
        elif isinstance(node, dict):
            result.append(node)
    return result


def extract_jsonld(html):
    """Return the JSON-LD blocks of one page with their types; bad blocks are recorded, not fatal."""
    parser = _ScriptParser()
    parser.feed(html)
    parser.close()
    blocks, errors, all_types, top_types = [], [], set(), []
    page_url = None
    for index, raw in enumerate(parser.blocks):
        try:
            data = json.loads(_strip_wrappers(raw))
        except ValueError as error:
            errors.append({"block": index, "error": f"invalid JSON: {error}"})
            continue
        blocks.append(data)
        _walk(data, all_types)
        for node in _top_nodes(data):
            node_types = _types(node.get("@type"))
            for name in node_types:
                if name not in top_types:
                    top_types.append(name)
            if page_url is None and any(name in PAGE_TYPES for name in node_types):
                url = node.get("url") or node.get("@id")
                page_url = url if isinstance(url, str) else None
    return {
        "url": page_url,
        "block_count": len(parser.blocks),
        "top_level_types": top_types,
        "types": sorted(all_types),
        "errors": errors,
        "blocks": blocks,
    }


def jsonld_report(pages):
    """Build jsonld.json from (source, html) pairs."""
    entries, union = [], set()
    for source, html in pages:
        entry = {"source": source, **extract_jsonld(html)}
        union.update(entry["types"])
        entries.append(entry)
    return {
        "page_count": len(entries),
        "types": sorted(union),
        "pages_without_jsonld": [entry["source"] for entry in entries if not entry["blocks"]],
        "pages": entries,
    }
