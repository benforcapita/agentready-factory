from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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
