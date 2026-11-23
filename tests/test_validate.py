from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from ipaddress import IPv6Address
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import PropertyMock, patch

import yaml

from arf.cli import main


SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "requests" / "sample.yaml"


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.request = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
        self.directory = self.enterContext(TemporaryDirectory())
        self.resolve = self.enterContext(patch("socket.getaddrinfo", autospec=True))
        self.resolve.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]

    def run_validate(self, path):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["validate", str(path)])
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_rejected(self, message):
        path = Path(self.directory) / "request.yaml"
        path.write_text(yaml.safe_dump(self.request), encoding="utf-8")
        code, stdout, stderr = self.run_validate(path)
        self.assertNotEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertIn(message, stderr)
        self.resolve.assert_not_called()

    def test_accepts_sample_request(self):
        code, stdout, stderr = self.run_validate(SAMPLE)
        self.assertEqual(code, 0, stderr)
        self.assertIn("Request is valid", stdout)
        self.assertEqual(stderr, "")
        self.resolve.assert_called_once_with("example.com.", None, type=socket.SOCK_STREAM)

    def test_rejects_unknown_key(self):
        self.request["unexpected"] = True
        self.assert_rejected("unexpected: Extra inputs are not permitted")

    def test_rejects_http_url(self):
        self.request["site"] = "http://example.com"
        self.assert_rejected("site: must be an HTTPS URL")

    def test_rejects_private_ip(self):
        for host in ["10.0.0.1", "[::ffff:10.0.0.1]", "[fec0::1]", "localhost."]:
            with self.subTest(host=host):
                self.request["site"] = f"https://{host}"
                self.assert_rejected("site: host must be public")

        self.resolve.return_value.append(
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.2", 0)),
        )
        code, _, stderr = self.run_validate(SAMPLE)
        self.assertNotEqual(code, 0)
        self.assertIn("site: host must resolve only to public addresses", stderr)

    def test_rejects_missing_musy_block(self):
        del self.request["musy"]
        self.assert_rejected("musy: required when trigger is musy_lead")

    def test_rejects_non_public_ipv6_literals(self):
        hosts = [
            "::ffff:0:127.0.0.1",
            "::ffff:127.0.0.1",
            "::ffff:10.0.0.1",
            "64:ff9b::7f00:1",
            "64:ff9b:1::7f00:1",
            "64:ff9b:1:7f00:1::",
            "::1",
            "fe80::1",
            "fc00::1",
            "fec0::1",
            "::",
            "::127.0.0.1",
            "ff0e::1",
            "2001:db8::1",
            "2001:0:5db8:d822:0:ffff:80ff:fffe",
            "2001:0:7f00:1:0:ffff:a247:27dd",
            "0:0:0:0:FFFF:0:7F00:0001",
        ]
        for host in hosts:
            with self.subTest(host=host):
                self.request["site"] = f"https://[{host}]"
                self.assert_rejected("site: host must be public")

    def test_rejects_6to4_literals_before_checking_is_global(self):
        with patch.object(IPv6Address, "is_global", new_callable=PropertyMock) as is_global:
            is_global.side_effect = AssertionError("6to4 must not consult IPv6Address.is_global")
            for host in ["2002:808:808::", "2002:7f00:1::"]:
                with self.subTest(host=host):
                    self.request["site"] = f"https://[{host}]"
                    self.assert_rejected("site: host must be public")

    def test_rejects_teredo_literals_before_checking_is_global(self):
        with patch.object(IPv6Address, "is_global", new_callable=PropertyMock) as is_global:
            is_global.side_effect = AssertionError("Teredo must not consult IPv6Address.is_global")
            for host in [
                "2001:0:4136:e378:8000:63bf:f7f7:f7f7",
                "2001:0:7f00:1:0:ffff:a247:27dd",
                "2001:0:5db8:d822:0:ffff:80ff:fffe",
                "2001::",
                "2001:0:ffff:ffff:ffff:ffff:ffff:ffff",
            ]:
                with self.subTest(host=host):
                    self.request["site"] = f"https://[{host}]"
                    self.assert_rejected("site: host must be public")

    def test_rejects_ietf_protocol_assignments_before_checking_is_global(self):
        # Policy: fail closed on all of 2001::/23, matching Python 3.12.0/3.12.3.
        # Python 3.12.4+ and 3.13 treat the hosts below as global.
        with patch.object(IPv6Address, "is_global", new_callable=PropertyMock) as is_global:
            is_global.side_effect = AssertionError(
                "2001::/23 must not consult IPv6Address.is_global"
            )
            for host in [
                "2001:1::1", "2001:1::2", "2001:1::3",
                "2001:3::1", "2001:3:ffff:ffff:ffff:ffff:ffff:ffff",
                "2001:4:112::1", "2001:4:112:ffff:ffff:ffff:ffff:ffff",
                "2001:20::1", "2001:2f:ffff:ffff:ffff:ffff:ffff:ffff",
                "2001:30::1", "2001:3f:ffff:ffff:ffff:ffff:ffff:ffff",
                "2001:2::1", "2001:10::1", "2001:1ff:ffff:ffff:ffff:ffff:ffff:ffff",
            ]:
                with self.subTest(host=host):
                    self.request["site"] = f"https://[{host}]"
                    self.assert_rejected("site: host must be public")

    def test_rejects_ietf_protocol_assignment_resolver_answers(self):
        for host in ["2001:1::1", "2001:3::1", "2001:4:112::1", "2001:20::1", "2001:30::1"]:
            with self.subTest(host=host):
                self.resolve.return_value = [
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (host, 0, 0, 0)),
                ]
                code, stdout, stderr = self.run_validate(SAMPLE)
                self.assertNotEqual(code, 0)
                self.assertEqual(stdout, "")
                self.assertIn("site: host must resolve only to public addresses", stderr)

    def test_rejects_non_public_embedded_ipv4_ranges(self):
        for ipv4 in [
            "0.0.0.0", "10.0.0.1", "100.64.0.1", "127.0.0.1",
            "169.254.169.254", "172.16.0.1", "192.168.1.1",
            "192.0.2.1", "198.18.0.1", "224.0.0.1", "240.0.0.1",
        ]:
            for prefix in ["::ffff:", "::ffff:0:", "64:ff9b::", "::"]:
                with self.subTest(prefix=prefix, ipv4=ipv4):
                    self.request["site"] = f"https://[{prefix}{ipv4}]"
                    self.assert_rejected("site: host must be public")

    def test_rejects_malformed_or_scoped_ipv6_literals(self):
        for host in [
            "[fe80::1%25eth0]", "[fe80::1%eth0]",
            "[2606:4700:4700::1111%25eth0]", "[::ffff:0:127.0.0.1].",
            "[::ffff:0:127.0.0.\uff11]", "[::ffff:0:127\u30020\u30020\u30021]",
            "[::ffff:0:127.0.0.1\u200b]", "[]", "[::ffff:0:127.0.0.999]",
        ]:
            with self.subTest(host=host):
                self.request["site"] = f"https://{host}"
                self.assert_rejected("site: must be an HTTPS URL")

    def test_accepts_public_ipv6_literals_without_dns(self):
        for host in [
            "2606:4700:4700::1111", "2001:4860:4860::8888", "2001:470::1",
            "2001:200::1", "::ffff:93.184.216.34",
            "::ffff:0:93.184.216.34", "64:ff9b::5db8:d822",
        ]:
            with self.subTest(host=host):
                self.request["site"] = f"https://[{host}]:443/path"
                path = Path(self.directory) / "request.yaml"
                path.write_text(yaml.safe_dump(self.request), encoding="utf-8")
                code, stdout, stderr = self.run_validate(path)
                self.assertEqual(code, 0, stderr)
                self.assertIn("Request is valid", stdout)
                self.assertEqual(stderr, "")
                self.resolve.assert_not_called()

    def test_rejects_non_public_ipv6_resolver_answers(self):
        for host in [
            "::ffff:0:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::",
            "2001:0:4136:e378:8000:63bf:f7f7:f7f7",
        ]:
            with self.subTest(host=host):
                self.resolve.return_value = [
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (host, 0, 0, 0)),
                ]
                code, stdout, stderr = self.run_validate(SAMPLE)
                self.assertNotEqual(code, 0)
                self.assertEqual(stdout, "")
                self.assertIn("site: host must resolve only to public addresses", stderr)

    def test_rejects_public_scan_without_ben_ok(self):
        self.request["allow_public_scan"] = True
        del self.request["ben_ok"]
        self.assert_rejected("ben_ok: required when allow_public_scan is true")

    def test_rejects_mode_above_phase(self):
        self.request["mode"] = "reviewed"
        self.assert_rejected("mode: reviewed exceeds phase shadow")

    def test_rejects_bad_language(self):
        self.request["language"] = "fr"
        self.assert_rejected("language: Input should be 'auto', 'he' or 'en'")

    def test_rejects_missing_site(self):
        del self.request["site"]
        self.assert_rejected("site: Field required")

    def test_accepts_fake_ip_resolver_answer(self):
        self.resolve.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:198.19.255.254", 0, 0, 0)),
        ]
        code, stdout, stderr = self.run_validate(SAMPLE)
        self.assertEqual(code, 0, stderr)
        self.assertIn("Request is valid", stdout)
        self.assertIn("fake-IP addresses (198.18.0.0/15); public address check skipped", stderr)

        self.resolve.return_value.append(
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
        )
        code, _, stderr = self.run_validate(SAMPLE)
        self.assertNotEqual(code, 0)
        self.assertIn("site: host must resolve only to public addresses", stderr)

        for host in ["198.18.0.1", "127.0.0.1", "localhost"]:
            with self.subTest(host=host):
                self.resolve.reset_mock()
                self.request["site"] = f"https://{host}"
                self.assert_rejected("site: host must be public")

    def test_rejects_shorthand_ipv4_literals(self):
        resolvers = {
            "public": self.resolve.return_value,
            "fake-IP": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 0))],
        }
        for mode, answer in resolvers.items():
            for host in ["127.1", "0x7f.1", "10.1", "0177.0.0.1", "0X7F000001", "2130706433"]:
                with self.subTest(resolver=mode, host=host):
                    self.resolve.reset_mock()
                    self.resolve.return_value = answer
                    self.request["site"] = f"https://{host}"
                    self.assert_rejected("site: host must be public")
            for host in ["0x7g.1", "1.2.3.4.5", "999.1"]:
                with self.subTest(resolver=mode, host=host):
                    self.resolve.reset_mock()
                    self.request["site"] = f"https://{host}"
                    self.assert_rejected("site: must be an HTTPS URL")

    def test_rejects_non_ascii_ipv4_literals(self):
        resolvers = {
            "public": self.resolve.return_value,
            "fake-IP": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 0))],
        }
        hosts = [
            "127.0.0.\uff11",
            "\uff11\uff12\uff17.\uff11",
            "127\u30021",
            "127\u30020\u30020\u30021",
            "127\uff0e0\uff0e0\uff0e1",
            "127.1\u200b",
            "127.0.0.1\u00ad",
            "1\u200d27.0.0.1",
            "0x7f.0x\uff11",
            "169.254.169.\uff12\uff15\uff14",
            "[::127.0.0.1]",
            "[64:ff9b::7f00:1]",
            "[64:ff9b::a9fe:a9fe]",
            "metadata.google.internal",
            "printer.local",
            "router.home.arpa",
        ]
        for mode, answer in resolvers.items():
            for host in hosts:
                with self.subTest(resolver=mode, host=host):
                    self.resolve.reset_mock()
                    self.resolve.return_value = answer
                    self.request["site"] = f"https://{host}"
                    self.assert_rejected("site: host must be public")

    def test_accepts_internationalized_domain(self):
        self.request["site"] = "https://b\u00fccher.example"
        path = Path(self.directory) / "request.yaml"
        path.write_text(yaml.safe_dump(self.request), encoding="utf-8")
        code, stdout, stderr = self.run_validate(path)
        self.assertEqual(code, 0, stderr)
        self.resolve.assert_called_once_with(
            "xn--bcher-kva.example.", None, type=socket.SOCK_STREAM
        )
