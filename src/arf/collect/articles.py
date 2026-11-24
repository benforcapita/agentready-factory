"""Article sampling: pick up to two article URLs from the sitemap and GET them.

The probed ``/sitemap.xml`` body (or the ``/sitemap_index.xml`` discovery body) is parsed;
a sitemap index contributes only its first child as one extra GET. Up to ``CAP`` URLs on
the canonical registrable domain whose path is not ``/`` are fetched newest-first by
``<lastmod>`` behind the same target()/pinning/fake-IP guard as everything else. The
selected paths land in ``articles.json`` so the orchestrator can feed them to the readers.
"""

import json
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit

from . import config
from .transport import Refused

CAP = 2
MAX_SKIPPED = 100
REDIRECTS = frozenset({301, 302, 303, 307, 308})


def _tag(element):
    return element.tag.rsplit("}")[-1]


def sitemap(body):
    """Parse a sitemap body into ("index", [loc]) or ("urlset", [(loc, lastmod)])."""
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError):
        return None, []
    kind = _tag(root)
    if kind == "sitemapindex":
        return "index", [loc.text.strip() for loc in root.iter()
                         if _tag(loc) == "loc" and loc.text and loc.text.strip()]
    if kind == "urlset":
        entries = []
        for url in root:
            loc = lastmod = ""
            for child in url:
                if _tag(child) == "loc" and child.text:
                    loc = child.text.strip()
                elif _tag(child) == "lastmod" and child.text:
                    lastmod = child.text.strip()
            if loc:
                entries.append((loc, lastmod))
        return "urlset", entries
    return None, []


def _on_domain(url, domain):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = parts.hostname or ""
    return parts.scheme in ("http", "https") and (host == domain or host.endswith("." + domain))


def _path(url):
    parts = urlsplit(url)
    return parts.path + ("?" + parts.query if parts.query else "")


def fetch(out, request, errors, bodies, domain):
    """Sample up to CAP article URLs and return the evidence.json manifest section.

    `bodies` are candidate sitemap bodies in preference order (probed ``/sitemap.xml``
    first, then the ``/sitemap_index.xml`` discovery body); the first one collected is
    used. `request` is the collector's guarded GET and `errors` its errors.json list.
    """
    kind, entries, skipped = None, [], []

    def skip(url, reason):
        if len(skipped) < MAX_SKIPPED:
            skipped.append({"url": url, "reason": reason})

    body = next((candidate for candidate in bodies if candidate), None)
    if body is not None:
        kind, entries = sitemap(body)
        if kind == "index":
            child = entries[0] if entries else None
            entries = []
            if child is None:
                skip("", "sitemap index has no children")
            elif not _on_domain(child, domain):
                skip(child, "sitemap index child off canonical domain")
            else:
                try:
                    status, _, _, fetched, _, _ = request(child, config.IDENTITIES[0][1])
                except Refused as error:
                    errors.append({"url": child, "status": "refused",
                                   "reason": f"sitemap index child: {error}"})
                    skip(child, f"sitemap index child refused: {error}")
                else:
                    if status == 200:
                        kind, entries = sitemap(fetched)
                    else:
                        skip(child, f"sitemap index child returned {status}")
    seen = set()
    candidates = []
    for loc, lastmod in entries:
        if loc in seen:
            continue
        seen.add(loc)
        if not _on_domain(loc, domain):
            skip(loc, "off canonical domain")
        elif _path(loc) in ("", "/"):
            skip(loc, "root path")
        else:
            candidates.append((loc, lastmod))
    candidates.sort(key=lambda item: item[1], reverse=True)
    chosen = candidates[:CAP]
    for loc, _ in candidates[CAP:]:
        skip(loc, "over article cap")

    rows = []
    (out / "articles").mkdir(exist_ok=True)
    for index, (selected, lastmod) in enumerate(chosen):
        url, row = selected, {"url": selected, "lastmod": lastmod,
                              "host": urlsplit(selected).hostname, "path": _path(selected)}
        for hop in range(config.MAX_REDIRECTS + 1):
            try:
                status, _, _, fetched, location, _ = request(url, config.IDENTITIES[0][1])
            except Refused as error:
                errors.append({"url": url, "status": "refused", "reason": f"article: {error}"})
                row.update({"status": "refused", "reason": str(error)})
                break
            filename = f"articles/{index}-{hop}.body"
            (out / filename).write_bytes(fetched)
            row.update({"url": url, "status": status, "size": len(fetched), "body": filename})
            if status not in REDIRECTS or not location:
                break
            if hop == config.MAX_REDIRECTS:
                errors.append({"url": url, "reason": "article redirect limit reached"})
                break
            try:
                url = urljoin(url, location)
            except ValueError as error:
                errors.append({"url": url, "status": "error",
                               "reason": f"invalid article redirect: {error}"})
                break
        rows.append(row)
    if body is None:
        reason = "no sitemap collected"
    elif not rows:
        reason = "no eligible article urls"
    else:
        reason = None
    result = {"sitemap": kind, "articles": rows, "paths": [row["path"] for row in rows],
              "skipped": skipped, "reason": reason}
    (out / "articles.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    return result
