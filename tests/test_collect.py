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
from arf.collect.doh import resolve_doh
from arf.collect.transport import GuardPolicy, PinnedHTTPConnection, PinnedHTTPSConnection, Refused
from arf.validation import InvalidRequest
from tests.collect_server import closing_server, local_policy, resolve, server


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict("os.environ", {}, clear=True))
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
        self.assertEqual(manifest["requests_made"], 82)
        self.assertEqual(manifest["max_requests"], 100)
        self.assertLessEqual(manifest["requests_made"], 100)
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

    def test_global_budget_preserves_rows_and_succeeds(self):
        manifest = self.run_collect(max_requests=10)
        self.assertEqual(sum(self.instance.hits.values()), 10)
        self.assertEqual(manifest["requests_made"], 10)
        self.assertEqual(manifest["max_requests"], 10)
        with (self.out / "probes.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 72)
        self.assertTrue(all(row["status"] != "skipped_budget" for row in rows[:10]))
        self.assertEqual({row["status"] for row in rows[10:]}, {"skipped_budget"})
        with (self.out / "discovery.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 10)
        self.assertEqual({row["status"] for row in rows}, {"skipped_budget"})
        self.assertEqual(json.loads((self.out / "errors.json").read_text()), [])

    def test_global_budget_counts_redirect_hops(self):
        self.instance.redirect = "/llms-full.txt"
        manifest = self.run_collect(max_requests=75)
        self.assertEqual(manifest["requests_made"], 75)
        self.assertEqual(sum(self.instance.hits.values()), 75)
        with (self.out / "discovery.csv").open() as stream:
            self.assertIn("skipped_budget", {row["status"] for row in csv.DictReader(stream)})

    def assert_failed_budget(self, error_rows=5):
        manifest = json.loads((self.out / "evidence.json").read_text())
        self.assertEqual(manifest["requests_made"], 5)
        self.assertEqual(manifest["max_requests"], 5)
        with (self.out / "probes.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 72)
        self.assertEqual(sum(row["status"] == "error" for row in rows), error_rows)
        self.assertEqual(sum(row["status"] == "skipped_budget" for row in rows), 72 - error_rows)
        with (self.out / "discovery.csv").open() as stream:
            self.assertEqual({row["status"] for row in csv.DictReader(stream)}, {"skipped_budget"})

    def test_failed_tls_handshakes_consume_global_budget(self):
        with closing_server() as instance:
            with self.assertRaisesRegex(OSError, "errors.json"):
                self.run_collect(scheme="https", port=instance.server_address[1], max_requests=5)
            self.assertEqual(instance.accepted, 5)
        self.assert_failed_budget()

    def test_failed_tcp_connections_consume_global_budget(self):
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            with self.assertRaisesRegex(OSError, "errors.json"):
                self.run_collect(port=unavailable.getsockname()[1], max_requests=5)
        self.assert_failed_budget()

    def test_fallback_addresses_cannot_bypass_global_budget(self):
        attempts = []

        class CountingSocket(socket.socket):
            def connect(sock, address):
                attempts.append(address)
                return super().connect(address)

        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            port = unavailable.getsockname()[1]
            resolver = Mock(return_value=resolve("shop.example", port) * 2)
            with patch("arf.collect.transport.socket.socket", CountingSocket):
                with self.assertRaisesRegex(OSError, "errors.json"):
                    self.run_collect(port=port, resolver=resolver, max_requests=5)
        self.assertEqual(len(attempts), 5)
        self.assert_failed_budget(error_rows=3)

    def test_preconnect_refusal_does_not_consume_budget(self):
        self.instance.redirect = "http://private.example/"

        def resolving(host, port, **kwargs):
            if host == "private.example":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))]
            return resolve(host, port, **kwargs)

        with self.assertRaisesRegex(OSError, "errors.json"):
            self.run_collect(resolver=resolving)
        manifest = json.loads((self.out / "evidence.json").read_text())
        self.assertEqual(manifest["requests_made"], 82)
        self.assertEqual(sum(self.instance.hits.values()), 82)
        self.assertNotIn("private.example", manifest["request_count"]["per_host"])

    def test_6to4_is_denied_even_if_ipaddress_considers_it_global(self):
        address = "2002:0808:0808::1"
        with patch("arf.collect.transport.validation_is_public", return_value=True), patch("arf.collect.transport.socket.socket") as sock:
            resolver = Mock()
            with self.assertRaisesRegex(Refused, "literal"):
                collect(f"https://[{address}]", self.out, resolver=resolver)
            resolver.assert_not_called()
            resolver.return_value = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))]
            with self.assertRaisesRegex(Refused, "public addresses"):
                self.run_collect(resolver=resolver, policy=GuardPolicy())
            connection = PinnedHTTPConnection("shop.example", 80,
                resolver=Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 80))]),
                policy=GuardPolicy(), timeout=1, fake_ip_policy="doh_verify",
                doh_resolver=Mock(return_value=[address]))
            with self.assertRaisesRegex(Refused, "DoH answers must all be public"):
                connection.connect()
            sock.assert_not_called()

    def test_environment_defaults_and_explicit_overrides(self):
        with patch.dict("os.environ", {"ARF_MAX_REQUESTS": "0", "ARF_FAKE_IP_POLICY": "doh_verify",
                                       "ARF_DOH_ENDPOINT": "https://resolver.example/query"}):
            manifest = self.run_collect()
            self.assertEqual(manifest["requests_made"], 0)
            self.assertEqual(manifest["fake_ip_policy"], "doh_verify")
            self.assertEqual(manifest["config"]["doh_endpoint"], "https://resolver.example/query")
            self.out = self.directory / "override"
            manifest = self.run_collect(max_requests=1, fake_ip_policy="refuse",
                                        doh_endpoint=config.DOH_ENDPOINT)
            self.assertEqual(manifest["requests_made"], 1)
            self.assertEqual(manifest["fake_ip_policy"], "refuse")
            self.assertEqual(manifest["config"]["doh_endpoint"], config.DOH_ENDPOINT)

    def test_invalid_configuration(self):
        for kwargs in ({"max_requests": -1}, {"max_requests": "ten"}, {"max_requests": True},
                       {"fake_ip_policy": "allow"}, {"doh_endpoint": "http://resolver.example/"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_collect(**kwargs)
        with patch.dict("os.environ", {"ARF_MAX_REQUESTS": "ten"}):
            with self.assertRaisesRegex(ValueError, "ARF_MAX_REQUESTS"):
                self.run_collect()
        self.assertEqual(sum(self.instance.hits.values()), 0)

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
        for destination in ("http://127.0.0.1/", "http://private.example/", "file:///etc/passwd", "http://[::1/"):
            with self.subTest(destination=destination), TemporaryDirectory() as directory:
                self.out = Path(directory) / "bundle"
                self.instance.redirect = destination
                def resolving(host, port, **kwargs):
                    if host == "private.example":
                        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))]
                    return resolve(host, port, **kwargs)
                before = sum(self.instance.hits.values())
                with self.assertRaisesRegex(OSError, "errors.json"):
                    self.run_collect(resolver=resolving)
                self.assertEqual(sum(self.instance.hits.values()) - before, 82)
                errors = json.loads((self.out / "errors.json").read_text())
                self.assertEqual(len(errors), 2)
                expected = "error" if destination == "http://[::1/" else "refused"
                self.assertEqual({error["status"] for error in errors}, {expected})
                with (self.out / "discovery.csv").open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(sum(row["status"] == expected for row in rows), 2)
                self.assertEqual(rows[-1]["status"], "200")

    def test_public_ip_literals_have_no_www_variant(self):
        for literal, host in (("93.184.216.34", "93.184.216.34"),
                              ("0x5db8d822", "93.184.216.34"),
                              ("[2001:4860:4860::8888]", "2001:4860:4860::8888")):
            with self.subTest(literal=literal), TemporaryDirectory() as directory:
                out = Path(directory) / "bundle"
                manifest = collect(f"https://{literal}", out, resolver=resolve, scheme="http",
                                   port=self.instance.server_port,
                                   policy=GuardPolicy({host: ("127.0.0.1/32",)}))
                self.assertEqual(manifest["hosts"], [host])
                self.assertEqual(manifest["requests_made"], 41)
                with (out / "probes.csv").open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 36)
                self.assertEqual({row["domain"] for row in rows}, {host})

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

    def test_fake_ip_verification_failures_never_connect(self):
        fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 80))]
        cases = [
            ("refuse", ["93.184.216.34"], fake),
            ("doh_verify", ["127.0.0.1"], fake),
            ("doh_verify", ["10.0.0.1"], fake),
            ("doh_verify", ["93.184.216.34", "10.0.0.1"], fake),
            ("doh_verify", ["::ffff:127.0.0.1"], fake),
            ("doh_verify", [], fake),
            ("doh_verify", ["not-an-ip"], fake),
            ("doh_verify", "93.184.216.34", fake),
            ("doh_verify", OSError("DoH offline"), fake),
            ("doh_verify", TimeoutError("timeout"), fake),
            ("doh_verify", ["93.184.216.34"], fake + [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]),
        ]
        for mode, answers, records in cases:
            with self.subTest(mode=mode, answers=answers), patch("arf.collect.transport.socket.socket") as sock:
                doh = Mock(side_effect=answers) if isinstance(answers, Exception) else Mock(return_value=answers)
                connection = PinnedHTTPConnection("shop.example", 80, resolver=Mock(return_value=records),
                    policy=GuardPolicy(), timeout=1, fake_ip_policy=mode,
                    doh_endpoint=config.DOH_ENDPOINT, doh_resolver=doh)
                with self.assertRaises(Refused):
                    connection.connect()
                sock.assert_not_called()
                if mode == "refuse" or len(records) > 1:
                    doh.assert_not_called()

    def test_fake_ip_doh_deadline_refuses_before_connect(self):
        def slow_doh(*args, **kwargs):
            sleep(0.2)
            return ["93.184.216.34"]

        with patch("arf.collect.transport.socket.socket") as sock:
            connection = PinnedHTTPConnection("shop.example", 80,
                resolver=Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 80))]),
                policy=GuardPolicy(), timeout=0.03, deadline=monotonic() + 0.03,
                fake_ip_policy="doh_verify", doh_endpoint=config.DOH_ENDPOINT, doh_resolver=slow_doh)
            started = monotonic()
            with self.assertRaisesRegex(Refused, "DoH verification failed: timeout"):
                connection.connect()
            self.assertLess(monotonic() - started, 0.15)
            sock.assert_not_called()

    def test_fake_ip_doh_public_answers_pin_proxy_and_record_manifest(self):
        connects = []

        class ProxySocket(socket.socket):
            def connect(proxy, address):
                connects.append(address)
                return super().connect(("127.0.0.1", address[1]))

        def fake_resolve(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", port))]

        doh = Mock(return_value=["93.184.216.34", "2606:4700:4700::1111"])
        with patch("arf.collect.transport.socket.socket", ProxySocket):
            manifest = self.run_collect(resolver=fake_resolve, policy=GuardPolicy(),
                                        fake_ip_policy="doh_verify", doh_resolver=doh)
        self.assertEqual(len(connects), 82)
        self.assertTrue(all(address[0] == "198.18.0.1" for address in connects))
        self.assertEqual(sum(self.instance.hits.values()), 82)
        self.assertEqual(doh.call_count, 82)
        self.assertEqual(manifest["fake_ip_policy"], "doh_verify")
        expected = {host: sorted(doh.return_value) for host in ("shop.example", "www.shop.example")}
        self.assertEqual(manifest["doh_answers"], expected)
        self.assertEqual(json.loads((self.out / "evidence.json").read_text())["doh_answers"], expected)

    def test_redirect_fake_ip_is_doh_checked_and_failure_continues(self):
        class ProxySocket(socket.socket):
            def connect(proxy, address):
                if address[0] == "198.18.0.1":
                    address = ("127.0.0.1", address[1])
                return super().connect(address)

        def resolving(host, port, **kwargs):
            if host == "redirect.example":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", port))]
            return resolve(host, port, **kwargs)

        self.instance.redirect = f"http://redirect.example:{self.instance.server_port}/landing"
        for answer in ("93.184.216.34", "10.0.0.1"):
            with self.subTest(answer=answer), TemporaryDirectory() as directory:
                self.out = Path(directory) / "bundle"
                doh = Mock(return_value=[answer])
                with patch("arf.collect.transport.socket.socket", ProxySocket):
                    if answer == "10.0.0.1":
                        with self.assertRaisesRegex(OSError, "errors.json"):
                            self.run_collect(resolver=resolving, fake_ip_policy="doh_verify", doh_resolver=doh)
                    else:
                        self.run_collect(resolver=resolving, fake_ip_policy="doh_verify", doh_resolver=doh)
                self.assertEqual(doh.call_count, 2)
                self.assertTrue(all(call.args[0] == "redirect.example" for call in doh.call_args_list))
                manifest = json.loads((self.out / "evidence.json").read_text())
                self.assertEqual(manifest["doh_answers"], {"redirect.example": [answer]})
                self.assertEqual(manifest["requests_made"], 82 if answer == "10.0.0.1" else 84)


class DoHJSONTests(unittest.TestCase):
    def resolve(self, payloads, status=200):
        responses = []
        for payload in payloads:
            response = Mock(status=status)
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            response.read1.side_effect = [body, b""]
            responses.append(response)
        with patch("arf.collect.doh.http.client.HTTPSConnection") as https:
            https.return_value.getresponse.side_effect = responses
            answers = resolve_doh("shop.example", endpoint=config.DOH_ENDPOINT, timeout=1)
        return answers, https

    def test_queries_both_types_over_verified_https(self):
        answers, https = self.resolve([
            {"Status": 0, "Answer": [{"type": 1, "data": "93.184.216.34"}]},
            {"Status": 0, "Answer": [{"type": 28, "data": "2606:4700:4700::1111"}]},
        ])
        self.assertEqual(answers, ["93.184.216.34", "2606:4700:4700::1111"])
        calls = https.return_value.request.call_args_list
        self.assertIn("type=A", calls[0].args[1])
        self.assertIn("type=AAAA", calls[1].args[1])
        for call in calls:
            self.assertIn("name=shop.example", call.args[1])
            self.assertEqual(call.kwargs["headers"], {"Accept": "application/dns-json"})
        context = https.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, 2)

    def test_empty_answer_set(self):
        answers, _ = self.resolve([{"Status": 0}, {"Status": 0, "Answer": []}])
        self.assertEqual(answers, [])

    def test_malformed_and_failed_responses(self):
        for payload in (b"not-json", [], {"Status": 3}, {"Status": True},
                        {"Status": 0, "TC": True}, {"Status": 0, "Answer": {}},
                        {"Status": 0, "Answer": ["bad"]},
                        {"Status": 0, "Answer": [{"type": 1, "data": "::1"}]},
                        {"Status": 0, "Answer": [{"type": 1, "data": "garbage"}]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.resolve([payload])
        with self.assertRaisesRegex(ValueError, "HTTP status"):
            self.resolve([{}], status=302)


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
