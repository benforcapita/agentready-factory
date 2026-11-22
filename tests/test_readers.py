from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from arf.readers import (
    ReaderError, extract_jsonld, jina_read, jsonld_report, page_url, parse_schema_validator,
    parse_tinyfish, read_site, replay, tinyfish_read,
)
from arf.readers.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "fixtures" / "golden" / "lehamim"
READERS = ROOT / "fixtures" / "readers" / "lehamim"


def no_network(*args, **kwargs):
    raise AssertionError("live network call attempted")


class RecordedFetch:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, headers, body))
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                return response
        return 404, b"not recorded"


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("urllib.request.urlopen", side_effect=no_network))
        self.enterContext(patch("socket.create_connection", side_effect=no_network))
        self.out = Path(self.enterContext(TemporaryDirectory()))

    def test_lehamim_jsonld_types(self):
        expected = {
            "jina_home.html": ["WebPage", "ImageObject", "BreadcrumbList", "WebSite"],
            "jina_branch.html": ["WebPage", "BreadcrumbList", "WebSite"],
            "jina_menucat.html": ["CollectionPage", "ImageObject", "BreadcrumbList", "WebSite"],
            "jina_product.html": ["WebPage", "ImageObject", "BreadcrumbList", "WebSite"],
            "jina_shop.html": ["WebPage", "ImageObject", "BreadcrumbList", "WebSite"],
        }
        for name, types in expected.items():
            with self.subTest(name=name):
                page = extract_jsonld((GOLDEN / name).read_text(encoding="utf-8"))
                self.assertEqual(page["top_level_types"], types)
                self.assertEqual(page["errors"], [])
                self.assertTrue(page["url"].startswith("https://www.lehamim.co.il/"))
        home = extract_jsonld((GOLDEN / "jina_home.html").read_text(encoding="utf-8"))
        self.assertEqual(home["url"], "https://www.lehamim.co.il/")

    def test_replay_writes_expected_jsonld(self):
        summary = replay(GOLDEN, self.out)
        produced = json.loads((self.out / "jsonld.json").read_text(encoding="utf-8"))
        self.assertEqual(produced, json.loads((READERS / "jsonld.json").read_text(encoding="utf-8")))
        self.assertTrue({"WebPage", "WebSite", "CollectionPage"} <= set(produced["types"]))
        self.assertEqual(produced["page_count"], 5)
        self.assertEqual(produced["pages_without_jsonld"], [])
        self.assertEqual(summary["schema_validator"]["fetch_error"], "NOT_FOUND")
        self.assertEqual((self.out / "sv_home.txt").read_bytes(), (GOLDEN / "sv_home.txt").read_bytes())
        self.assertIn("invalid JSON", summary["tinyfish"]["error"])

    def test_bad_and_missing_jsonld_are_recorded(self):
        html = ('<script type="application/ld+json">{bad</script>'
                '<script type="Application/LD+JSON; charset=utf-8"><!-- {"@type": ["Organization", "Brand"]} --></script>'
                '<script type="text/javascript">{"@type": "Ignored"}</script>')
        page = extract_jsonld(html)
        self.assertEqual(page["block_count"], 2)
        self.assertEqual(page["types"], ["Brand", "Organization"])
        self.assertEqual(page["errors"][0]["block"], 0)
        report = jsonld_report([("empty.html", "<html></html>"), ("x.html", html)])
        self.assertEqual(report["pages_without_jsonld"], ["empty.html"])

    def test_sv_home(self):
        parsed = parse_schema_validator((GOLDEN / "sv_home.txt").read_text(encoding="utf-8"))
        self.assertEqual(parsed["fetch_error"], "NOT_FOUND")
        self.assertEqual(parsed["num_objects"], 0)
        with self.assertRaises(ReaderError):
            parse_schema_validator(")]}'\nnot json")

    def test_tinyfish_recording(self):
        parsed = parse_tinyfish((READERS / "tinyfish_fetch.json").read_text(encoding="utf-8"))
        self.assertEqual(parsed["ok_urls"], ["https://www.lehamim.co.il/"])
        self.assertEqual(parsed["failed_urls"], ["https://www.lehamim.co.il/llms.txt"])
        self.assertEqual(parsed["errors"][0]["error"], "page_not_found")
        with self.assertRaises(ReaderError):
            parse_tinyfish((GOLDEN / "tinyfish_fetch2.json").read_text(encoding="utf-8"))

    def test_tinyfish_without_key_skips_call(self):
        fetch = RecordedFetch({})
        result = tinyfish_read(["https://www.lehamim.co.il/"], api_key=None, fetch=fetch)
        self.assertIn("skipped", result)
        self.assertEqual(fetch.calls, [])

    def test_jina_adapter(self):
        html = (GOLDEN / "jina_home.html").read_bytes()
        fetch = RecordedFetch({"https://r.jina.ai/": (200, html)})
        result = jina_read("https://www.lehamim.co.il/", api_key="test-key", fetch=fetch)
        method, url, headers, _ = fetch.calls[0]
        self.assertEqual((method, url), ("GET", "https://r.jina.ai/https://www.lehamim.co.il/"))
        self.assertEqual(headers["X-Return-Format"], "html")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertTrue(result["ok"])

    def test_page_url_stays_on_site(self):
        self.assertEqual(page_url("https://www.lehamim.co.il/x", "/shop/"), "https://www.lehamim.co.il/shop/")
        for path in ("https://evil.example/", "//evil.example/", "http://www.lehamim.co.il/"):
            with self.subTest(path=path), self.assertRaises(ReaderError):
                page_url("https://www.lehamim.co.il", path)

    def test_read_site_with_recorded_services(self):
        fetch = RecordedFetch({
            "https://r.jina.ai/https://www.lehamim.co.il/shop/": (200, (GOLDEN / "jina_shop.html").read_bytes()),
            "https://r.jina.ai/https://www.lehamim.co.il/": (200, (GOLDEN / "jina_home.html").read_bytes()),
            "https://api.fetch.tinyfish.ai": (200, (READERS / "tinyfish_fetch.json").read_bytes()),
            "https://validator.schema.org/": (200, (GOLDEN / "sv_home.txt").read_bytes()),
        })
        summary = read_site("https://www.lehamim.co.il", ["/", "/shop/"], self.out,
                            fetch=fetch, tinyfish_key="test-key")
        self.assertEqual(summary["jsonld"]["pages"], 2)
        self.assertEqual([item["file"] for item in summary["jina"]], ["jina_home.html", "jina_shop.html"])
        tinyfish = json.loads((self.out / "tinyfish.json").read_text(encoding="utf-8"))
        self.assertEqual(tinyfish["failed_urls"], ["https://www.lehamim.co.il/llms.txt"])
        self.assertEqual(summary["schema_validator"]["fetch_error"], "NOT_FOUND")
        self.assertTrue((self.out / "sv_home.txt").exists())

    def test_module_entry_point(self):
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main(["replay", str(GOLDEN), "--out", str(self.out)])
        self.assertEqual(code, 0)
        self.assertIn("CollectionPage", json.loads(stdout.getvalue())["jsonld"]["types"])


if __name__ == "__main__":
    unittest.main()
