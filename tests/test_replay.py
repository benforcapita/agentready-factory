import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest import mock

from arf.cli import main
from arf.replay import NetworkDisabled, ReplayError, network_disabled, replay, sha256, structure, verify_bundle

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "fixtures" / "golden" / "lehamim"
PROMPTS = ROOT / "fixtures" / "prompts" / "recorded"
ACK = ROOT / "fixtures" / "lint" / "lehamim" / "lint-ack.json"

LEHAMIM_SECTIONS = [
    "h1: דוח מוכנות לסוכני AI (AgentReady) — מאפיית לחמים",
    "h2: תקציר מנהלים",
    "h2: 1. למה זה חשוב ללחמים",
    "h2: 2. מה מצאנו",
    "h2: 3. מה עובד היום",
    "h2: 4. מה אנחנו מציעים",
    "h2: 5. הצעד הבא",
    "h2: נספח — מקורות ומועדי בדיקה (שעון ישראל, 20.11.2026)",
]
LEHAMIM_TABLES = [[3] * 6, [4] * 8]


def golden_structure():
    return structure((GOLDEN / "report.html").read_text(encoding="utf-8"))


class NetworkDisabledTest(unittest.TestCase):
    def test_refuses_connections_and_lookups(self):
        with network_disabled():
            with self.assertRaises(NetworkDisabled):
                socket.create_connection(("192.0.2.1", 443), timeout=1)
            with self.assertRaises(NetworkDisabled):
                socket.getaddrinfo("example.com", 443)
            with socket.socket() as sock, self.assertRaises(NetworkDisabled):
                sock.connect(("192.0.2.1", 443))
        self.assertIsNot(socket.create_connection, None)
        self.assertEqual(socket.getaddrinfo("127.0.0.1", 80)[0][4][0], "127.0.0.1")

    def test_replay_steps_run_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            def reader(*args):
                socket.getaddrinfo("lehamim.co.il", 443)

            with mock.patch("arf.replay.replay_readers", reader), self.assertRaises(NetworkDisabled):
                replay(GOLDEN, Path(temp) / "out", PROMPTS, ack=ACK)


class StructureTest(unittest.TestCase):
    def test_lehamim_golden_structure(self):
        self.assertEqual(golden_structure(), {"sections": LEHAMIM_SECTIONS, "tables": LEHAMIM_TABLES})

    def test_counts_cells_per_row(self):
        html = "<h2>A <b>b</b></h2><table><tr><th>1</th><th>2</th></tr><tr><td>x</td><td>y</td></tr></table><h1>C</h1>"
        self.assertEqual(structure(html), {"sections": ["h2: A b", "h1: C"], "tables": [[2, 2]]})


class VerifyBundleTest(unittest.TestCase):
    def copy(self, temp):
        bundle = Path(temp) / "bundle"
        shutil.copytree(GOLDEN, bundle)
        return bundle

    def test_golden_bundle_is_intact(self):
        files = {entry["file"] for entry in verify_bundle(GOLDEN)}
        self.assertTrue({"report.md", "report.html", "notes-for-ben.md", "report.pdf"} <= files)

    def test_rejects_changed_missing_and_unsafe_files(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = self.copy(temp)
            with (bundle / "report.md").open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with self.assertRaisesRegex(ReplayError, "report.md does not match"):
                verify_bundle(bundle)
            (bundle / "report.md").unlink()
            with self.assertRaisesRegex(ReplayError, "missing: report.md"):
                verify_bundle(bundle)
            (bundle / "SHA256SUMS.txt").write_text(f"{'0' * 64}  ../outside.txt\n", encoding="utf-8")
            with self.assertRaisesRegex(ReplayError, "outside the bundle"):
                verify_bundle(bundle)
            (bundle / "SHA256SUMS.txt").unlink()
            with self.assertRaisesRegex(ReplayError, "SHA256SUMS.txt"):
                verify_bundle(bundle)


class ReplayGoldenTest(unittest.TestCase):
    """CI golden test: replay the frozen Lehamim bundle offline."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.shadow_out, cls.final_out = root / "shadow", root / "final"
        cls.shadow = replay(GOLDEN, cls.shadow_out, PROMPTS, run_id="golden", ack=ACK)
        cls.final = replay(GOLDEN, cls.final_out, PROMPTS, final=True, ack=ACK)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_golden_checks_pass(self):
        for result in (self.shadow, self.final):
            checks = result["checks"]
            self.assertTrue(result["passed"], checks)
            self.assertTrue(checks["sections_match"])
            self.assertTrue(checks["tables_match"])
            self.assertTrue(checks["lint_passed"])
            self.assertEqual(checks["lint_hard_fails"], 0)
            self.assertTrue(2 <= checks["pdf_pages"] <= 4, checks["pdf_pages"])
        self.assertTrue(self.final["checks"]["html_identical"])
        self.assertNotIn("html_identical", self.shadow["checks"])

    def test_section_order_and_table_shapes_match_lehamim(self):
        for out in (self.shadow_out, self.final_out):
            actual = structure((out / "report.html").read_text(encoding="utf-8"))
            self.assertEqual(actual, {"sections": LEHAMIM_SECTIONS, "tables": LEHAMIM_TABLES})

    def test_writes_replay_artifacts(self):
        for out, result in ((self.shadow_out, self.shadow), (self.final_out, self.final)):
            written = json.loads((out / "replay.json").read_text(encoding="utf-8"))
            self.assertEqual(written, result)
            for name, digest in result["outputs"].items():
                self.assertEqual(sha256(out / name), digest, name)
            self.assertTrue((out / "readers" / "jsonld.json").is_file())
            self.assertEqual(set(result["prompts"]), {"analyze", "judge", "write-notes", "write-report"})
            used = json.loads((out / "prompts-used.json").read_text(encoding="utf-8"))
            self.assertEqual(len(used["prompts"]), 4)
            lint = json.loads((out / "lint.json").read_text(encoding="utf-8"))
            self.assertTrue(lint["passed"])
            self.assertEqual(lint["summary"]["unacknowledged_warning"], 0)

    def test_shadow_replay_carries_draft_marker(self):
        report = (self.shadow_out / "report.md").read_text(encoding="utf-8")
        self.assertTrue(report.startswith("<!-- arf:draft run=golden -->\n"))
        self.assertIn("טיוטה — לא לשליחה · DRAFT", (self.shadow_out / "report.html").read_text(encoding="utf-8"))
        self.assertEqual((self.final_out / "report.md").read_bytes(), (GOLDEN / "report.md").read_bytes())
        self.assertEqual((self.final_out / "report.html").read_bytes(), (GOLDEN / "report.html").read_bytes())

    def test_replay_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            again = replay(GOLDEN, Path(temp), PROMPTS, run_id="golden", ack=ACK)
        for name in ("report.md", "report.html", "lint.json", "prompts-used.json"):
            self.assertEqual(again["outputs"][name], self.shadow["outputs"][name], name)


class ReplayFailureTest(unittest.TestCase):
    def test_structure_drift_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "bundle"
            shutil.copytree(GOLDEN, bundle)
            html = (bundle / "report.html").read_text(encoding="utf-8")
            first = html.index("<h2")
            html = html[:first] + "<h2>extra</h2><table><tr><td>x</td></tr></table>" + html[first:]
            (bundle / "report.html").write_text(html, encoding="utf-8")
            sums = (bundle / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
            sums = [f"{sha256(bundle / 'report.html')}  report.html" if line.endswith(" report.html") else line
                    for line in sums]
            (bundle / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
            result = replay(bundle, Path(temp) / "out", PROMPTS, final=True, ack=ACK)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["sections_match"])
        self.assertFalse(result["checks"]["tables_match"])
        self.assertFalse(result["checks"]["html_identical"])

    def test_without_ack_lint_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            result = replay(GOLDEN, Path(temp), PROMPTS, final=True)
        self.assertFalse(result["checks"]["lint_passed"])
        self.assertFalse(result["passed"])

    def test_refuses_non_empty_output(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "old.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ReplayError, "not empty"):
                replay(GOLDEN, Path(temp), PROMPTS, ack=ACK)


class ReplayCliTest(unittest.TestCase):
    def run_cli(self, *args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["replay", *map(str, args)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_passes_on_golden(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "out"
            code, stdout, stderr = self.run_cli(GOLDEN, "--out", out, "--prompts", PROMPTS, "--ack", ACK, "--final")
            self.assertEqual(code, 0, stderr)
            self.assertIn("arf replay: passed", stdout)
            self.assertTrue(json.loads((out / "replay.json").read_text(encoding="utf-8"))["passed"])

    def test_cli_reports_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            code, _, stderr = self.run_cli(Path(temp), "--out", Path(temp) / "out", "--prompts", PROMPTS)
        self.assertEqual(code, 1)
        self.assertIn("arf replay: frozen bundle needs SHA256SUMS.txt", stderr)


if __name__ == "__main__":
    unittest.main()
