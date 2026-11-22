from contextlib import redirect_stderr, redirect_stdout
import csv
from datetime import datetime
import hashlib
from io import StringIO
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from arf.cli import main
from arf.collect import collect, config
from arf.collect.transport import GuardPolicy, PinnedHTTPConnection, PinnedHTTPSConnection, Refused
from arf.validation import InvalidRequest
from tests.collect_server import local_policy, resolve, server


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.out = self.directory / "bundle"
        self.instance = self.enterContext(server())

    def run_collect(self, **kwargs):
        options = dict(resolver=resolve, scheme="http", port=self.instance.server_port,
                       policy=local_policy())
        options.update(kwargs)
        return collect("https://www.shop.example/somewhere", self.out, **options)

    def test_end_to_end(self):
        manifest = self.run_collect()
        with (self.out / "probes.csv").open(newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0], ["domain", "path", "agent", "status", "server", "cf_ray", "size"])
        expected = [(host, path, agent) for host in ("shop.example", "www.shop.example")
                    for path in config.PATHS for agent, _ in config.IDENTITIES]
        self.assertEqual([tuple(row[:3]) for row in rows[1:]], expected)
        self.assertEqual(len(rows) - 1, 72)
        for row in rows[1:]:
            self.assertEqual(row[3], "301" if row[1] == "/" else "200")
            self.assertEqual("CollectFixture/1.0", row[4])
            self.assertEqual(row[5:], ["fixture-ray", str(len(b"fixture body\n"))])
        self.assertNotIn("/never-follow-probe", self.instance.paths)
        self.assertEqual(manifest["request_count"]["per_host"], dict(self.instance.hits))
        self.assertEqual(manifest["request_count"]["total"], sum(self.instance.hits.values()))
        self.assertTrue(all(count == 41 and count <= 60 for count in self.instance.hits.values()))
        for headers in self.instance.headers_seen:
            self.assertNotIn("Cookie", headers)
            self.assertNotIn("Authorization", headers)
        for key in ("started_at", "finished_at"):
            stamp = datetime.fromisoformat(manifest[key])
            self.assertEqual(stamp.utcoffset(), stamp.astimezone(ZoneInfo("Asia/Jerusalem")).utcoffset())
        files = {p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual([f["path"] for f in manifest["files"]], sorted(files - {"evidence.json"}))
        for entry in manifest["files"]:
            data = (self.out / entry["path"]).read_bytes()
            self.assertEqual(entry["bytes"], len(data))
            self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(json.loads((self.out / "evidence.json").read_text()), json.loads(json.dumps(manifest)))

    def test_cap_stops_before_next_request(self):
        with self.assertRaisesRegex(Refused, "cap"):
            self.run_collect(cap=2)
        self.assertEqual(sum(self.instance.hits.values()), 2)

    def test_nonempty_refused(self):
        self.out.mkdir()
        (self.out / "keep").write_text("unchanged")
        with self.assertRaisesRegex(Refused, "empty"):
            self.run_collect()
        self.assertEqual((self.out / "keep").read_text(), "unchanged")
        self.assertEqual(sum(self.instance.hits.values()), 0)

    def test_empty_existing_directory(self):
        self.out.mkdir()
        self.run_collect()

    def test_failed_requests_keep_rows_and_manifest(self):
        with patch.object(PinnedHTTPConnection, "connect", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "errors.json"):
                self.run_collect()
        with (self.out / "probes.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 72)
        self.assertEqual({r["status"] for r in rows}, {"error"})
        self.assertEqual({r["server"] for r in rows}, {""})
        self.assertTrue((self.out / "evidence.json").is_file())
        self.assertIn("offline", (self.out / "errors.json").read_text())

    def test_default_policy_rejects_all_unsafe_answers_before_connect(self):
        for index, addresses in enumerate((["127.0.0.1"], ["10.0.0.1"], ["172.16.0.1"], ["192.168.1.1"],
                          ["169.254.169.254"], ["::1"], ["fe80::1"], ["198.18.0.1"],
                          ["93.184.216.34", "127.0.0.1"], ["100.64.0.1"], ["224.0.0.1"],
                          ["0.0.0.0"], ["::ffff:127.0.0.1"], ["64:ff9b::7f00:1"])):
            with self.subTest(addresses=addresses):
                self.out = self.directory / f"bundle-{index}"
                resolver = Mock(return_value=[(socket.AF_INET6 if ":" in a else socket.AF_INET,
                    socket.SOCK_STREAM, 6, "", (a, self.instance.server_port)) for a in addresses])
                with patch("arf.collect.transport.socket.socket") as sock:
                    with self.assertRaises(Refused):
                        self.run_collect(policy=GuardPolicy(), resolver=resolver)
                    sock.assert_not_called()
                resolver.assert_called_once()
        self.assertEqual(sum(self.instance.hits.values()), 0)

    def test_literal_and_special_use_bypasses(self):
        for host in ("127.1", "0x7f.1", "0177.0.0.1", "2130706433", "１２７。０。０。１",
                     "127\u200b.1", "[::127.0.0.1]", "[::ffff:127.0.0.1]", "localhost", "x.local",
                     "x.internal", "x.home.arpa", "x.test", "x.invalid", "x.onion", "x.alt"):
            with self.subTest(host=host):
                resolver = Mock()
                with self.assertRaises(Refused):
                    collect(f"https://{host}", self.out, resolver=resolver)
                resolver.assert_not_called()
        self.assertEqual(sum(self.instance.hits.values()), 0)

    def test_discovery_redirect_guards(self):
        for destination in ("http://127.0.0.1/", "http://private.example/", "file:///etc/passwd"):
            with self.subTest(destination=destination), TemporaryDirectory() as directory:
                self.out = Path(directory) / "bundle"
                self.instance.redirect = destination
                def resolving(host, port, **kwargs):
                    if host == "private.example":
                        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))]
                    return resolve(host, port, **kwargs)
                before = sum(self.instance.hits.values())
                with self.assertRaises(Refused):
                    self.run_collect(resolver=resolving)
                self.assertEqual(sum(self.instance.hits.values()) - before, 74)

    def test_discovery_follows_three_redirects_and_counts_each(self):
        self.instance.redirect = "/llms-full.txt"
        with self.assertRaisesRegex(OSError, "errors.json"):
            self.run_collect()
        manifest = json.loads((self.out / "evidence.json").read_text())
        self.assertEqual(manifest["request_count"]["total"], 88)
        self.assertEqual(sum(self.instance.hits.values()), 88)

    def test_body_cap(self):
        with patch.object(config, "MAX_BODY_BYTES", 4):
            self.run_collect()
        with (self.out / "probes.csv").open() as stream:
            self.assertEqual({r["size"] for r in csv.DictReader(stream)}, {"4"})

    def test_explicit_site_port_and_seam_override(self):
        for override in (None, self.instance.server_port):
            with self.subTest(override=override), TemporaryDirectory() as directory:
                site_port = self.instance.server_port if override is None else 1
                manifest = collect(f"https://shop.example:{site_port}", Path(directory) / "bundle",
                                   resolver=resolve, scheme="http", port=override, policy=local_policy())
                self.assertEqual(manifest["request_count"]["total"], 82)
                with (Path(directory) / "bundle" / "discovery.csv").open() as stream:
                    self.assertTrue(all(f":{self.instance.server_port}/" in row["url"]
                                        for row in csv.DictReader(stream)))
        self.assertTrue(all(h["Host"].endswith(f":{self.instance.server_port}")
                            for h in self.instance.headers_seen))

    def test_wall_clock_timeout_on_trickling_body(self):
        self.instance.trickle = 0.02
        with patch.object(config, "TIMEOUT", 0.12), patch.object(config, "PATHS", ("/robots.txt",)), patch.object(config, "IDENTITIES", config.IDENTITIES[:1]), patch.object(config, "DISCOVERY_PATHS", ()):
            started = monotonic()
            with self.assertRaisesRegex(OSError, "errors.json"):
                self.run_collect()
            elapsed = monotonic() - started
        self.assertLess(elapsed, 0.8)
        with (self.out / "probes.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["status"] for row in rows}, {"error"})
        self.assertTrue(all(0 < int(row["size"]) < len(b"fixture body\n") for row in rows))
        errors = json.loads((self.out / "errors.json").read_text())
        self.assertEqual([error["reason"] for error in errors], ["timeout", "timeout"])

    def test_wall_clock_timeout_includes_dns(self):
        def slow_resolve(*args, **kwargs):
            sleep(0.3)
            return resolve(*args, **kwargs)

        with patch.object(config, "TIMEOUT", 0.04), patch.object(config, "PATHS", ("/",)), patch.object(config, "IDENTITIES", config.IDENTITIES[:1]), patch.object(config, "DISCOVERY_PATHS", ()):
            started = monotonic()
            with self.assertRaisesRegex(OSError, "errors.json"):
                self.run_collect(resolver=slow_resolve)
            self.assertLess(monotonic() - started, 0.25)
        self.assertEqual(sum(self.instance.hits.values()), 0)
        errors = json.loads((self.out / "errors.json").read_text())
        self.assertEqual([error["reason"] for error in errors], ["timeout", "timeout"])

    def test_dns_pinning_and_tls_hostname(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        resolver = Mock(return_value=records)
        with patch("arf.collect.transport.socket.socket") as make_socket, patch("arf.collect.transport.ssl.create_default_context") as context:
            connection = PinnedHTTPSConnection("shop.example", None, resolver=resolver,
                                               policy=GuardPolicy(), timeout=10)
            connection.connect()
            resolver.assert_called_once_with("shop.example", 443, type=socket.SOCK_STREAM)
            make_socket.return_value.connect.assert_called_once_with(("93.184.216.34", 443))
            context.return_value.wrap_socket.assert_called_once_with(make_socket.return_value, server_hostname="shop.example")
            connection.close()


class CollectCLITests(unittest.TestCase):
    def invoke(self, extra=()):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["collect", "request.yaml", "--out", "bundle", *extra])
        return code, stdout.getvalue(), stderr.getvalue()

    @patch("arf.cli.collect")
    @patch("arf.cli.validate_request")
    def test_validate_then_collect_and_warnings(self, validate, collector):
        validate.return_value = (Mock(site="https://shop.example"), ["example warning"])
        code, stdout, stderr = self.invoke(["--phase", "reviewed"])
        self.assertEqual(code, 0)
        self.assertIn("Evidence collected", stdout)
        self.assertIn("warning: example warning", stderr)
        validate.assert_called_once_with(Path("request.yaml"), phase="reviewed")
        collector.assert_called_once_with("https://shop.example", Path("bundle"))

    @patch("arf.cli.collect")
    @patch("arf.cli.validate_request", side_effect=InvalidRequest("bad request"))
    def test_invalid_request_does_not_collect(self, validate, collector):
        code, _, stderr = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("bad request", stderr)
        collector.assert_not_called()

    @patch("arf.cli.validate_request", return_value=(Mock(site="https://shop.example"), []))
    def test_collect_errors(self, validate):
        for error in (Refused("unsafe"), OSError("offline")):
            with patch("arf.cli.collect", side_effect=error):
                code, _, stderr = self.invoke()
                self.assertEqual(code, 1)
                self.assertIn(str(error), stderr)
