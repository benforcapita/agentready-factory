"""Evidence readers: Jina HTML, TinyFish fetch, JSON-LD extraction and schema.org validator."""

import json
from pathlib import Path

from .adapters import (
    JINA_ENDPOINT, SCHEMA_VALIDATOR_ENDPOINT, TINYFISH_ENDPOINT, ReaderError, jina_read,
    load_recorded_json, page_url, parse_schema_validator, parse_tinyfish, schema_validate,
    tinyfish_read, urllib_fetch,
)
from .jsonld import extract_jsonld, jsonld_report

__all__ = [
    "JINA_ENDPOINT", "SCHEMA_VALIDATOR_ENDPOINT", "TINYFISH_ENDPOINT", "ReaderError",
    "extract_jsonld", "jina_read", "jsonld_report", "load_recorded_json", "page_url",
    "parse_schema_validator", "parse_tinyfish", "read_site", "replay", "schema_validate",
    "tinyfish_read", "urllib_fetch", "write_json",
]


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _slug(path):
    slug = "".join(c if c.isalnum() else "-" for c in path.strip("/")).strip("-")
    return slug or "home"


def _summary(out, jsonld, tinyfish, validator, extra=None):
    readers = {
        "jsonld": {"file": "jsonld.json", "types": jsonld["types"], "pages": jsonld["page_count"]},
        "tinyfish": tinyfish,
        "schema_validator": validator,
        **(extra or {}),
    }
    write_json(out / "readers.json", readers)
    return readers


def replay(bundle, out):
    """Rebuild reader outputs from a recorded bundle (jina_*.html, tinyfish*.json, sv_home.txt)."""
    bundle, out = Path(bundle), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    pages = [(path.name, path.read_text(encoding="utf-8")) for path in sorted(bundle.glob("jina_*.html"))]
    jsonld = jsonld_report(pages)
    write_json(out / "jsonld.json", jsonld)

    tinyfish = None
    recordings = sorted(bundle.glob("tinyfish*.json"))
    if recordings:
        try:
            parsed = parse_tinyfish(recordings[-1].read_text(encoding="utf-8"))
        except ReaderError as error:
            tinyfish = {"source": recordings[-1].name, "error": str(error)}
        else:
            write_json(out / "tinyfish.json", parsed)
            tinyfish = {"file": "tinyfish.json", "ok_urls": parsed["ok_urls"], "failed_urls": parsed["failed_urls"]}

    validator = None
    sv_home = bundle / "sv_home.txt"
    if sv_home.exists():
        text = sv_home.read_text(encoding="utf-8")
        (out / "sv_home.txt").write_text(text, encoding="utf-8")
        validator = {"file": "sv_home.txt", **parse_schema_validator(text)}
    return _summary(out, jsonld, tinyfish, validator)


def read_site(site, paths, out, *, fetch=urllib_fetch, jina_key=None, tinyfish_key=None,
              tinyfish_endpoint=TINYFISH_ENDPOINT):
    """Read live pages through Jina/TinyFish and the schema.org validator; failures are recorded."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    urls = [page_url(site, path) for path in paths]
    pages, jina = [], []
    for path, url in zip(paths, urls):
        try:
            result = jina_read(url, api_key=jina_key, fetch=fetch)
        except ReaderError as error:
            jina.append({"url": url, "error": str(error)})
            continue
        name = f"jina_{_slug(path)}.html"
        (out / name).write_text(result["html"], encoding="utf-8")
        jina.append({"url": url, "status": result["status"], "file": name})
        if result["ok"]:
            pages.append((name, result["html"]))
    jsonld = jsonld_report(pages)
    write_json(out / "jsonld.json", jsonld)

    try:
        tinyfish = tinyfish_read(urls, api_key=tinyfish_key, endpoint=tinyfish_endpoint, fetch=fetch)
    except (ReaderError, ValueError) as error:
        tinyfish = {"reader": "tinyfish", "error": str(error), "results": [], "errors": []}
    write_json(out / "tinyfish.json", tinyfish)

    try:
        status, text = schema_validate(urls[0], fetch=fetch)
        (out / "sv_home.txt").write_text(text, encoding="utf-8")
        validator = {"file": "sv_home.txt", "status": status, **parse_schema_validator(text)}
    except (ReaderError, IndexError) as error:
        validator = {"error": str(error)}
    return _summary(out, jsonld, {"file": "tinyfish.json"}, validator, {"jina": jina})
