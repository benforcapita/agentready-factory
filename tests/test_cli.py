from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from arf import cli
from arf.lint.__main__ import main as lint_main
from arf.render.__main__ import main as render_main
from arf.scaffold.__main__ import main as scaffold_main


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "fixtures" / "golden" / "lehamim"
LINT = ROOT / "fixtures" / "lint"
SCAFFOLD = ROOT / "fixtures" / "scaffold"
PROMPTS = ROOT / "fixtures" / "prompts"
RUN_ID = "arf-example-com-20261120-1000-ab12"
MODULES = {"render": render_main, "lint": lint_main, "scaffold": scaffold_main}


def call(function, args):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = function([str(arg) for arg in args])
        except SystemExit as error:
            code = error.code
    return code, stdout.getvalue(), stderr.getvalue()


class DelegatedCommandTests(unittest.TestCase):
    def assert_same(self, command, *args, out=None):
        """Run `arf <command> ...` and the module entry point, each into its own run folder."""
        with TemporaryDirectory() as directory:
            results = []
            for name, function in (("cli", lambda argv: cli.main([command, *argv])), ("module", MODULES[command])):
                target = Path(directory) / name
                argv = [target if arg is out else arg for arg in args]
                code, stdout, stderr = call(function, argv)
                files = {}
                if target.is_dir():
                    files = {p.relative_to(target).as_posix(): p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file()}
                elif target.is_file():
                    files = {"<out>": target.read_bytes()}
                results.append((code, stdout.replace(str(target), "<out>"), stderr.replace(str(target), "<out>"), files))
            self.assertEqual(results[0], results[1])
            return results[0]

    def test_help_matches_module_entry_points(self):
        for command in MODULES:
            with self.subTest(command=command):
                code, stdout, stderr, _ = self.assert_same(command, "--help")
                self.assertEqual(code, 0)
                self.assertIn("--out", stdout)
                self.assertEqual(stderr, "")

    def test_usage_errors_match(self):
        for command in MODULES:
            with self.subTest(command=command):
                code, _, stderr, _ = self.assert_same(command)
                self.assertEqual(code, 2)
                self.assertIn("required", stderr)

    def test_render_lehamim(self):
        code, stdout, _, files = self.assert_same(
            "render", GOLDEN / "report.md", "--out", OUT, "--title", "דוח AgentReady — מאפיית לחמים", "--final", "--no-pdf",
            out=OUT,
        )
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "Wrote <out>/report.html\n")
        self.assertEqual(files["report.html"], (GOLDEN / "report.html").read_bytes())

    def test_render_missing_report(self):
        code, _, stderr, _ = self.assert_same("render", ROOT / "missing.md", "--out", OUT, "--no-pdf", out=OUT)
        self.assertEqual(code, 1)
        self.assertTrue(stderr.startswith("arf render: "))

    def test_lint_lehamim(self):
        code, stdout, _, files = self.assert_same(
            "lint", GOLDEN, "--final", "--ack", LINT / "lehamim" / "lint-ack.json", "--out", OUT, out=OUT
        )
        self.assertEqual(code, 0)
        self.assertTrue(stdout.startswith("PASS: 0 hard fails"))
        self.assertEqual(files["<out>"], (LINT / "expected" / "lehamim.json").read_bytes())

    def test_lint_seeded_failure(self):
        seed = LINT / "seeded" / "01-price-in-offer"
        code, stdout, _, files = self.assert_same("lint", seed, "--out", OUT, out=OUT)
        self.assertEqual(code, 1)
        self.assertTrue(stdout.startswith("FAIL: "))
        self.assertEqual(files["<out>"], (LINT / "expected" / "01-price-in-offer.json").read_bytes())

    def test_lint_input_error(self):
        code, _, stderr, _ = self.assert_same("lint", GOLDEN, "--out", GOLDEN / "report.md")
        self.assertEqual(code, 2)
        self.assertIn("must not overwrite", stderr)

    def test_scaffold_writes_run_folder(self):
        code, stdout, _, files = self.assert_same(
            "scaffold", SCAFFOLD / "request-he.yaml", "--out", OUT, "--run-id", RUN_ID, "--prompts", PROMPTS / "recorded",
            out=OUT,
        )
        self.assertEqual(code, 0)
        self.assertIn("wrote <out>/report.md", stdout)
        self.assertLessEqual({"notes-for-ben.md", "prompts-used.json", "report.md"}, set(files))

    def test_scaffold_prompt_selection_error(self):
        code, _, stderr, _ = self.assert_same(
            "scaffold", SCAFFOLD / "request-he.yaml", "--out", OUT, "--run-id", RUN_ID, "--prompts", PROMPTS / "zero-production",
            out=OUT,
        )
        self.assertEqual(code, 1)
        self.assertTrue(stderr.startswith("PromptSelectionError: "))


OUT = Path("<run-folder>")


class TopLevelTests(unittest.TestCase):
    def test_help_lists_wired_commands(self):
        code, stdout, _ = call(cli.main, ["--help"])
        self.assertEqual(code, 0)
        for command in ("render", "lint", "scaffold", "replay"):
            self.assertIn(command, stdout)

    def test_replay_requires_bundle_and_out(self):
        code, _, stderr = call(cli.main, ["replay"])
        self.assertEqual(code, 2)
        self.assertNotIn("not implemented", stderr)

if __name__ == "__main__":
    unittest.main()
