"""Advertised agent endpoints: pull MCP/API URLs out of fetched catalog bodies and GET them.

Discovery bodies (`ai-catalog.json`, `ai-plugin.json`, `mcp.json`, `poppy.json`) and the
probed `llms.txt` are scanned for http(s) URLs sitting in fields or on lines that name
mcp, api, openapi, server or endpoint. Unique URLs, in discovery order, are fetched with
the browser identity behind the same target()/pinning/fake-IP guard as everything else,
capped at `CAP`; URLs past the cap are written as `skipped_cap` rows.
"""

import csv
import json
import re
from urllib.parse import urljoin, urlsplit

from . import config
from .transport import Refused

KEYWORDS = ("mcp", "api", "openapi", "server", "endpoint")
SOURCES = (
    "/.well-known/ai-catalog.json",
    "/.well-known/ai-plugin.json",
    "/.well-known/mcp.json",
    "/poppy.json",
    "/.well-known/poppy.json",
    "/llms.txt",
)
CAP = 2
REDIRECTS = frozenset({301, 302, 303, 307, 308})
URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def _urls_in(text):
    return [match.rstrip(".,;:!?") for match in URL.findall(text or "")]


def _from_json(data):
    """URLs under keys whose names mention a keyword, in document order."""
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            if any(word in str(key).lower() for word in KEYWORDS):
                found += _urls_in(json.dumps(value))
            found += _from_json(value)
    elif isinstance(data, list):
        for item in data:
            found += _from_json(item)
    return found


def advertised_urls(body):
    """URLs advertised by one catalog body: JSON fields, else keyword lines (llms.txt)."""
    text = body.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if data is not None:
        return _from_json(data)
    return [url for line in text.splitlines()
            if any(word in line.lower() for word in KEYWORDS)
            for url in _urls_in(line)]


def fetch(out, request, errors, bodies):
    """Fetch up to CAP advertised endpoints. Returns the manifest section for evidence.json.

    `bodies` maps a source path (e.g. `/.well-known/mcp.json`) to its already-fetched body.
    `request` is the collector's guarded GET; `errors` is the collector's errors.json list.
    """
    urls = []
    for source in SOURCES:
        for url in advertised_urls(bodies.get(source, b"")):
            if url not in {advertised for _, advertised in urls}:
                urls.append((source, url))
    rows = []
    (out / "advertised").mkdir(exist_ok=True)
    with (out / "advertised.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("source_file", "url", "host", "status", "size", "body"))
        for index, (source, url) in enumerate(urls):
            if index >= CAP:
                writer.writerow((source, url, "", "skipped_cap", 0, ""))
                continue
            for hop in range(config.MAX_REDIRECTS + 1):
                try:
                    status, _, _, body, location, _ = request(url, config.IDENTITIES[0][1])
                except Refused as error:
                    errors.append({"url": url, "source_file": source,
                                   "status": "refused", "reason": str(error)})
                    writer.writerow((source, url, urlsplit(url).hostname or "", "refused", 0, ""))
                    break
                filename = f"advertised/{index}-{hop}.body"
                (out / filename).write_bytes(body)
                writer.writerow((source, url, urlsplit(url).hostname or "", status, len(body), filename))
                rows.append({"source_file": source, "url": url,
                             "host": urlsplit(url).hostname, "status": status,
                             "size": len(body), "body": filename})
                if status not in REDIRECTS or not location:
                    break
                if hop == config.MAX_REDIRECTS:
                    errors.append({"url": url, "source_file": source,
                                   "reason": "advertised redirect limit reached"})
                    break
                try:
                    url = urljoin(url, location)
                except ValueError as error:
                    errors.append({"url": url, "source_file": source,
                                   "status": "error", "reason": f"invalid redirect: {error}"})
                    break
    return {"candidates": [{"source_file": source, "url": url} for source, url in urls],
            "cap": CAP, "fetched": rows}
