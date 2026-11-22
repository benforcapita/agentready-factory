from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from arf.cli import main
from arf.notion import SCHEMAS, check_object, check_recorded


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "notion"
RECORDED = FIXTURES / "recorded"


def run(*argv):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


def load(key):
    return json.loads((RECORDED / f"{key}.json").read_text(encoding="utf-8"))


def schema(key):
    return next(item for item in SCHEMAS if item.key == key)


class CheckSchemaTests(unittest.TestCase):
    def test_recorded_responses_pass(self):
        self.assertEqual(check_recorded(RECORDED), {item.key: [] for item in SCHEMAS})
        code, stdout, stderr = run("notion", "check-schema", str(RECORDED))
        self.assertEqual(code, 0, stderr)
        self.assertIn("Schema check passed", stdout)

    def test_every_schema_has_a_fixture(self):
        for item in SCHEMAS:
            self.assertTrue((RECORDED / f"{item.key}.json").is_file(), item.key)

    def test_renamed_property_fails_naming_it(self):
        code, stdout, stderr = run("notion", "check-schema", str(FIXTURES / "renamed-property"))
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("FAIL  Runs", stderr)
        self.assertIn("Runs: missing property 'State' (select)", stderr)
        self.assertIn("Schema check failed", stderr)
        self.assertNotIn("FAIL  Tasks", stderr)

    def test_changed_type_is_reported(self):
        response = load("tasks")
        response["properties"]["Site"]["type"] = "rich_text"
        self.assertEqual(
            check_object(schema("tasks"), response),
            ["Tasks: property 'Site' has type 'rich_text', expected 'url'"],
        )

    def test_missing_select_option_is_reported(self):
        response = load("runs")
        options = response["properties"]["State"]["select"]["options"]
        response["properties"]["State"]["select"]["options"] = [
            option for option in options if option["name"] != "guardrail_blocked"
        ]
        self.assertEqual(
            check_object(schema("runs"), response),
            ["Runs: property 'State' is missing option 'guardrail_blocked'"],
        )

    def test_extra_properties_are_allowed(self):
        response = load("artifacts")
        response["properties"]["Notes"] = {"id": "x", "name": "Notes", "type": "rich_text"}
        self.assertEqual(check_object(schema("artifacts"), response), [])

    def test_wrong_object_kind_is_reported(self):
        self.assertEqual(
            check_object(schema("tasks"), load("factory-config")),
            ["Tasks: expected a database, found 'page'"],
        )

    def test_missing_and_invalid_files_fail(self):
        with TemporaryDirectory() as directory:
            copy = Path(directory)
            for path in RECORDED.glob("*.json"):
                shutil.copy(path, copy)
            (copy / "artifacts.json").unlink()
            (copy / "tasks.json").write_text("{", encoding="utf-8")
            code, _, stderr = run("notion", "check-schema", directory)
        self.assertEqual(code, 1)
        self.assertIn("Artifacts: no recorded response at", stderr)
        self.assertIn("Tasks: cannot read", stderr)

    def test_check_schema_requires_a_directory(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main(["notion", "check-schema"])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main(["notion"])


if __name__ == "__main__":
    unittest.main()
