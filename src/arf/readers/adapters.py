"""Reader adapters: Jina (rendered HTML), TinyFish (fetch API) and the schema.org validator.

Every adapter takes an injectable ``fetch`` so tests replay recorded fixtures; nothing here
runs a live call unless a caller passes the default urllib transport.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 60
JINA_ENDPOINT = "https://r.jina.ai/"
TINYFISH_ENDPOINT = "https://api.fetch.tinyfish.ai"
SCHEMA_VALIDATOR_ENDPOINT = "https://validator.schema.org/validate"
SCHEMA_VALIDATOR_PREFIX = ")]}'"


class ReaderError(Exception):
    pass


def urllib_fetch(method, url, headers, body=None):
    """Default transport: returns (status, bytes); HTTP errors are returned, not raised."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_BYTES + 1)[:MAX_BYTES]
    except urllib.error.HTTPError as error:
        return error.code, error.read(MAX_BYTES)
    except (urllib.error.URLError, OSError) as error:
        raise ReaderError(f"{url}: {error}") from None


def page_url(site, path):
    """Join a request site and a path, refusing anything that leaves the site or is not https."""
    base = urllib.parse.urlsplit(site)
    url = urllib.parse.urljoin(f"{base.scheme}://{base.netloc}/", path)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.netloc.lower() != base.netloc.lower():
        raise ReaderError(f"{path}: must stay on {base.netloc} over https")
    return url


def jina_read(url, *, api_key=None, fetch=urllib_fetch):
    headers = {"X-Return-Format": "html", "Accept": "text/html"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    status, body = fetch("GET", JINA_ENDPOINT + url, headers)
    text = body.decode("utf-8", errors="replace")
    return {"reader": "jina", "url": url, "status": status, "ok": status == 200, "html": text}


def load_recorded_json(text):
    """Decode the first JSON value in text; recorded captures may carry a tool wrapper line."""
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ReaderError("no JSON value found")
    try:
        return json.JSONDecoder().raw_decode(text, start)[0]
    except ValueError as error:
        raise ReaderError(f"invalid JSON: {error}") from None


def parse_tinyfish(text):
    """Normalise a TinyFish fetch response into per-URL results and errors."""
    data = load_recorded_json(text)
    if isinstance(data, dict) and "result" in data and "results" not in data:
        data = data["result"]
    if not isinstance(data, dict):
        raise ReaderError("TinyFish response is not an object")
    results = [item for item in data.get("results") or [] if isinstance(item, dict)]
    errors = [item for item in data.get("errors") or [] if isinstance(item, dict)]
    return {
        "reader": "tinyfish",
        "results": results,
        "errors": errors,
        "ok_urls": [item.get("url") for item in results],
        "failed_urls": [item.get("url") for item in errors],
    }


def tinyfish_read(urls, *, api_key, endpoint=TINYFISH_ENDPOINT, fetch=urllib_fetch):
    if not api_key:
        return {"reader": "tinyfish", "skipped": "TINYFISH_API_KEY not set", "results": [], "errors": []}
    body = json.dumps({"urls": list(urls)}).encode()
    headers = {"Content-Type": "application/json", "X-API-Key": api_key}
    status, response = fetch("POST", endpoint, headers, body)
    text = response.decode("utf-8", errors="replace")
    if status != 200:
        return {"reader": "tinyfish", "status": status, "error": text[:500], "results": [], "errors": []}
    return {"status": status, **parse_tinyfish(text)}


def parse_schema_validator(text):
    """Parse validator.schema.org output (sv_home.txt), which starts with an XSSI guard."""
    text = text.lstrip()
    if text.startswith(SCHEMA_VALIDATOR_PREFIX):
        text = text[len(SCHEMA_VALIDATOR_PREFIX):]
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ReaderError(f"invalid schema validator JSON: {error}") from None
    if not isinstance(data, dict):
        raise ReaderError("schema validator response is not an object")
    return {
        "url": data.get("url"),
        "fetch_error": data.get("fetchError"),
        "num_objects": data.get("numObjects", 0),
        "total_errors": data.get("totalNumErrors", 0),
        "total_warnings": data.get("totalNumWarnings", 0),
        "triple_groups": len(data.get("tripleGroups") or []),
    }


def schema_validate(url, *, fetch=urllib_fetch):
    body = urllib.parse.urlencode({"url": url}).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    status, response = fetch("POST", SCHEMA_VALIDATOR_ENDPOINT, headers, body)
    return status, response.decode("utf-8", errors="replace")
