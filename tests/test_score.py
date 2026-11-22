from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import yaml

from arf.request import Request
from arf.score import api_lookup, collect_scores, musy_passthrough, public_scan, scan_gate
from arf.score.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "fixtures" / "golden" / "lehamim"
SAMPLE = ROOT / "fixtures" / "requests" / "sample.yaml"
SCORE = ROOT / "fixtures" / "score"


def load(path, **changes):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(changes)
    return Request.model_validate(data)


def no_network(*args, **kwargs):
    raise AssertionError("live network call attempted")


def recorded(status, path):
    calls = []

    def fetch(method, url, headers, body=None):
        calls.append(url)
        return status, path.read_bytes()

    fetch.calls = calls
    return fetch


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("urllib.request.urlopen", side_effect=no_network))
        self.popen = self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("spawned")))
        self.out = Path(self.enterContext(TemporaryDirectory()))
        self.not_found = recorded(404, GOLDEN / "isag_api.json")

    def spy(self):
        return Mock(return_value=subprocess.CompletedProcess(
            [], 0, stdout=(GOLDEN / "isag_cli.json").read_text(encoding="utf-8"), stderr=""))

    def test_allow_public_scan_defaults_false(self):
        data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
        del data["allow_public_scan"], data["ben_ok"]
        self.assertFalse(Request.model_validate(data).allow_public_scan)

    def test_gate_off_never_spawns_cli(self):
        runner = self.spy()
        result = public_scan(load(SAMPLE), runner=runner)
        self.assertFalse(result["ran"])
        self.assertEqual(result["gate"]["reason"], "allow_public_scan is false (default)")
        runner.assert_not_called()
        summary = collect_scores(load(SAMPLE), self.out, fetch=self.not_found, runner=runner)
        runner.assert_not_called()
        self.popen.assert_not_called()
        self.assertFalse((self.out / "isag_cli.json").exists())
        self.assertFalse(summary["is_agentic"]["public_scan"]["ran"])

    def test_refused_without_ben_ok(self):
        for ben_ok in (None, "", "   "):
            with self.subTest(ben_ok=ben_ok):
                request = load(SCORE / "request-public-scan-no-ben-ok.yaml", ben_ok=ben_ok)
                runner = self.spy()
                summary = collect_scores(request, self.out, fetch=self.not_found, runner=runner)
                runner.assert_not_called()
                gate = summary["is_agentic"]["public_scan"]["gate"]
                self.assertFalse(gate["allowed"])
                self.assertIn("ben_ok is missing", gate["reason"])
        self.popen.assert_not_called()

    def test_gate_allows_with_ben_ok(self):
        request = load(SCORE / "request-public-scan.yaml")
        runner = self.spy()
        summary = collect_scores(request, self.out, fetch=self.not_found, runner=runner)
        runner.assert_called_once()
        command = runner.call_args.args[0]
        self.assertEqual(command, ["npx", "--yes", "is-agentic@1.0.1", "www.lehamim.co.il", "--json"])
        self.assertEqual(runner.call_args.kwargs["env"]["NO_COLOR"], "1")
        self.assertEqual(summary["is_agentic"]["score"], 2)
        self.assertEqual(summary["is_agentic"]["from"], "is-agentic-cli")
        self.assertTrue(scan_gate(request)["ben_ok"].startswith("Ben, 2026-11-22"))
        self.assertTrue((self.out / "isag_cli.json").exists())

    def test_cli_failure_is_recorded(self):
        request = load(SCORE / "request-public-scan.yaml")
        runner = Mock(return_value=subprocess.CompletedProcess([], 1, stdout='{"code":"x"}', stderr="boom"))
        result = public_scan(request, runner=runner)
        self.assertEqual((result["exit_code"], result["error"]), (1, "boom"))
        runner = Mock(side_effect=FileNotFoundError("npx"))
        self.assertIn("npx", public_scan(request, runner=runner)["error"])

    def test_api_404_is_recorded_not_fatal(self):
        result = api_lookup("https://www.lehamim.co.il", fetch=self.not_found)
        self.assertEqual((result["status"], result["found"], result["error"]), (404, False, "report_not_found"))
        self.assertEqual(self.not_found.calls,
                         ["https://is-agentic.com/api/v1/report?url=https%3A%2F%2Fwww.lehamim.co.il"])
        summary = collect_scores(load(SAMPLE), self.out, fetch=self.not_found, runner=self.spy())
        self.assertIsNone(summary["is_agentic"]["score"])
        self.assertEqual(json.loads((self.out / "isag_api.json").read_text())["status"], 404)

    def test_api_found_skips_scan(self):
        found = recorded(200, GOLDEN / "isag_cli.json")
        runner = self.spy()
        summary = collect_scores(load(SCORE / "request-public-scan.yaml"), self.out, fetch=found, runner=runner)
        runner.assert_not_called()
        self.assertEqual((summary["is_agentic"]["score"], summary["is_agentic"]["from"]), (2, "is-agentic-api"))
        self.assertEqual(summary["is_agentic"]["public_scan"]["skipped"], "stored report found")

    def test_api_errors_are_recorded(self):
        def broken(method, url, headers, body=None):
            from arf.readers import ReaderError
            raise ReaderError("offline")

        self.assertEqual(api_lookup("https://x.example", fetch=broken)["error"], "offline")
        self.assertEqual(api_lookup("https://x.example", fetch=lambda *a: (500, b"oops"))["error"],
                         "unexpected status 500")

    def test_musy_passthrough(self):
        musy = musy_passthrough(load(SAMPLE))
        self.assertEqual((musy["agent_ready"], musy["poppy"]), (30, 0))
        self.assertEqual(musy["claimed_gaps"], ["llms.txt missing", "no ai-catalog.json"])
        self.assertIsNone(musy_passthrough(load(SAMPLE, trigger="named_site", musy=None)))

    def test_module_entry_point_gate_off(self):
        resolve = self.enterContext(patch("socket.getaddrinfo", autospec=True))
        resolve.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        run = self.enterContext(patch("subprocess.run", side_effect=AssertionError("spawned")))
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main([str(SAMPLE), "--out", str(self.out), "--api-fixture", str(GOLDEN / "isag_api.json")])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.popen.assert_not_called()
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["is_agentic"]["api"]["status"], 404)
        self.assertFalse(summary["is_agentic"]["public_scan"]["ran"])

    def test_module_entry_point_rejects_missing_ben_ok(self):
        resolve = self.enterContext(patch("socket.getaddrinfo", autospec=True))
        resolve.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        with redirect_stdout(StringIO()), patch("sys.stderr", new=StringIO()) as stderr:
            code = main([str(SCORE / "request-public-scan-no-ben-ok.yaml"), "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertIn("ben_ok: required", stderr.getvalue())
        self.popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
