import hashlib
import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from arf.prompts import (
    FACTORY,
    STEPS,
    PromptSelectionError,
    PromptVersion,
    RecordedPromptVersions,
    page_id,
    prompts_used,
    select_prompts,
    write_prompts_used,
)
from arf.scaffold.__main__ import main as scaffold_main


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "fixtures" / "prompts"
ANALYZE_V3 = "https://www.notion.so/AgentReady-analyze-v3-3fecd12e056c55f6bd08f2801d35f1e6"
JUDGE_V2 = "https://www.notion.so/AgentReady-judge-v2-999b7a55e5e8521eb445f0ac6db3deec"
WRITE_NOTES_V2 = "https://www.notion.so/AgentReady-write-notes-v2-ce96a351630557eb8d57b3ce57a06279"
OTHER_JUDGE = "https://www.notion.so/AgentReady-judge-v1-87a7bb455ee95e3aaf02785aa2b5c598"


def recorded(name="recorded"):
    return RecordedPromptVersions(PROMPTS / name)


class MemoryReader:
    def __init__(self, rows):
        self._rows = list(rows)

    def rows(self):
        return self._rows

    def row(self, url):
        for row in self._rows:
            if row.page_id == page_id(url):
                return row
        raise PromptSelectionError(url)


class SelectionTest(unittest.TestCase):
    def test_one_production_row_per_step(self):
        selected = select_prompts(recorded())
        self.assertEqual(list(selected), list(STEPS))
        self.assertEqual({item.selected_by for item in selected.values()}, {"production"})
        self.assertEqual(
            {step: item.prompt.version_id for step, item in selected.items()},
            {
                "analyze": f"{FACTORY}/analyze/v2",
                "write-report": f"{FACTORY}/write-report/v2",
                "write-notes": f"{FACTORY}/write-notes/v1",
                "judge": f"{FACTORY}/judge/v1",
            },
        )
        self.assertNotEqual(selected["judge"].prompt.url, OTHER_JUDGE, "rows of other factories are ignored")

    def test_zero_production_rows(self):
        with self.assertRaisesRegex(PromptSelectionError, r"write-notes: expected exactly one production row .* found 0"):
            select_prompts(recorded("zero-production"))

    def test_two_production_rows(self):
        with self.assertRaises(PromptSelectionError) as caught:
            select_prompts(recorded("two-production"))
        self.assertIn("judge: expected exactly one production row", str(caught.exception))
        self.assertIn("found 2", str(caught.exception))
        self.assertIn(JUDGE_V2, str(caught.exception))

    def test_override_wins(self):
        selected = select_prompts(recorded(), {"analyze": ANALYZE_V3})
        self.assertEqual(selected["analyze"].selected_by, "override")
        self.assertEqual(selected["analyze"].prompt.status, "testing")
        self.assertEqual(selected["analyze"].prompt.url, ANALYZE_V3)
        self.assertEqual(selected["write-report"].selected_by, "production")

    def test_override_resolves_ambiguous_and_missing_steps(self):
        self.assertEqual(select_prompts(recorded("two-production"), {"judge": JUDGE_V2})["judge"].prompt.url, JUDGE_V2)
        notes = select_prompts(recorded("zero-production"), {"write-notes": WRITE_NOTES_V2})["write-notes"]
        self.assertEqual(notes.prompt.version_id, f"{FACTORY}/write-notes/v2")

    def test_override_url_forms(self):
        dashed = "https://www.notion.so/3fecd12e-056c-55f6-bd08-f2801d35f1e6?pvs=4"
        self.assertEqual(select_prompts(recorded(), {"analyze": dashed})["analyze"].prompt.url, ANALYZE_V3)

    def test_bad_overrides(self):
        cases = {
            "for 'AgentReady report factory' step 'analyze'": {"judge": ANALYZE_V3},
            "is for 'Other factory'": {"judge": OTHER_JUDGE},
            "not found": {"analyze": "https://www.notion.so/" + "0" * 32},
            "not a Prompt Versions row URL": {"analyze": "https://www.notion.so/no-id"},
            "unknown process step": {"draft": ANALYZE_V3},
        }
        for message, overrides in cases.items():
            with self.subTest(message), self.assertRaisesRegex(PromptSelectionError, message):
                select_prompts(recorded(), overrides)

    def test_incomplete_or_missing_recordings_fail(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "prompts"
            shutil.copytree(PROMPTS / "recorded", root)
            block = root / "blocks" / "3d899112fe695e2498ab7e675915c3fc.json"
            data = json.loads(block.read_text())
            data["has_more"] = True
            block.write_text(json.dumps(data))
            with self.assertRaisesRegex(PromptSelectionError, "incomplete"):
                select_prompts(RecordedPromptVersions(root))
            block.unlink()
            with self.assertRaisesRegex(PromptSelectionError, "could not read"):
                select_prompts(RecordedPromptVersions(root))
        with self.assertRaisesRegex(PromptSelectionError, "could not read"):
            select_prompts(RecordedPromptVersions(PROMPTS / "missing"))

    def test_empty_prompt_text_fails(self):
        rows = [
            PromptVersion(f"{index:032x}", f"https://www.notion.so/{index:032x}", step, "production", step, FACTORY, "v1", "" if step == "judge" else "text")
            for index, step in enumerate(STEPS, 1)
        ]
        with self.assertRaisesRegex(PromptSelectionError, "judge: prompt text is empty"):
            select_prompts(MemoryReader(rows))

    def test_reader_interface(self):
        rows = [
            PromptVersion(f"{index:032x}", f"https://www.notion.so/{index:032x}", step, "production", step, FACTORY, "v7", f"{step} שלום\n")
            for index, step in enumerate(STEPS, 1)
        ]
        selected = select_prompts(MemoryReader(rows))
        self.assertEqual(selected["analyze"].prompt.sha256, hashlib.sha256("analyze שלום\n".encode()).hexdigest())


JUDGE_PRODUCTION = "3d899112-fe69-5e24-98ab-7e675915c3fc"
REQUEST_HE = ROOT / "fixtures" / "scaffold" / "request-he.yaml"


class RecordedRowIdTest(unittest.TestCase):
    """QA N2: a recorded row id is a Notion page id, never a path."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tmp = Path(self.directory.name)
        self.root = self.tmp / "bundle" / "prompts"
        shutil.copytree(PROMPTS / "recorded", self.root)
        self.evil = self.tmp / "bundle" / "outside" / "evil.json"
        self.evil.parent.mkdir(parents=True)
        self.evil.write_text(json.dumps({"results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "EVIL"}]}}]}))

    def set_judge_id(self, row_id):
        query_path = self.root / "query.json"
        query = json.loads(query_path.read_text())
        for page in query["results"]:
            if page["id"] == JUDGE_PRODUCTION:
                page["id"] = row_id
        query_path.write_text(json.dumps(query))

    def run_scaffold(self):
        out = self.tmp / "run"
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = scaffold_main([str(REQUEST_HE), "--out", str(out), "--run-id", "n2", "--prompts", str(self.root)])
        return code, stderr.getvalue(), out

    def assert_rejected(self, row_id, reason):
        self.set_judge_id(row_id)
        with self.assertRaisesRegex(PromptSelectionError, reason) as caught:
            select_prompts(RecordedPromptVersions(self.root))
        self.assertIn(repr(row_id), str(caught.exception))
        code, stderr, out = self.run_scaffold()
        self.assertEqual(code, 1)
        self.assertIn("PromptSelectionError", stderr)
        self.assertIn(reason, stderr)
        self.assertFalse(out.exists())

    def test_dot_dot_traversal_id(self):
        self.assert_rejected("../../outside/evil", "id is a path")

    def test_absolute_path_id(self):
        self.assert_rejected(str(self.evil.with_suffix("")), "id is a path")

    def test_backslash_path_id(self):
        self.assert_rejected("..\\..\\outside\\evil", "id is a path")

    def test_non_hex_id(self):
        self.assert_rejected("3d899112-fe69-5e24-98ab-7e675915c3fz", "not a 32-hex Notion page id")

    def test_id_that_differs_from_url_page_id(self):
        self.assert_rejected("87a7bb45-5ee9-5e3a-af02-785aa2b5c598", "does not match the page id")

    def test_out_of_tree_symlink(self):
        block = self.root / "blocks" / "3d899112fe695e2498ab7e675915c3fc.json"
        block.unlink()
        block.symlink_to(self.evil)
        with self.assertRaisesRegex(PromptSelectionError, "resolves outside blocks/"):
            select_prompts(RecordedPromptVersions(self.root))
        code, stderr, out = self.run_scaffold()
        self.assertEqual(code, 1)
        self.assertIn("resolves outside blocks/", stderr)
        self.assertFalse(out.exists())

    def test_blocks_directory_symlinked_out_of_tree(self):
        outside = self.root.parent / "outside_blocks"
        (self.root / "blocks").rename(outside)
        (outside / "3d899112fe695e2498ab7e675915c3fc.json").write_text(self.evil.read_text())
        (self.root / "blocks").symlink_to("../outside_blocks", target_is_directory=True)
        with self.assertRaisesRegex(PromptSelectionError, "[0-9a-f]{32} resolves outside blocks/"):
            select_prompts(RecordedPromptVersions(self.root))
        code, stderr, out = self.run_scaffold()
        self.assertEqual(code, 1)
        self.assertIn("resolves outside blocks/", stderr)
        self.assertNotIn("EVIL", stderr)
        self.assertFalse(out.exists())

    def test_dashed_and_undashed_ids_are_accepted(self):
        self.set_judge_id(JUDGE_PRODUCTION.replace("-", ""))
        selected = select_prompts(RecordedPromptVersions(self.root))
        self.assertEqual(selected["judge"].prompt.page_id, "3d899112fe695e2498ab7e675915c3fc")


class PromptsUsedTest(unittest.TestCase):
    def test_hashes_match_text_used(self):
        selected = select_prompts(recorded(), {"analyze": ANALYZE_V3})
        with TemporaryDirectory() as directory:
            run = Path(directory)
            path = write_prompts_used(selected, run, "arf-example-com-20261120-1000-ab12")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data, prompts_used(selected, "arf-example-com-20261120-1000-ab12"))
            self.assertEqual([item["step"] for item in data["prompts"]], list(STEPS))
            for item in data["prompts"]:
                text = selected[item["step"]].prompt.text
                self.assertEqual(item["sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
                self.assertEqual(item["sha256"], hashlib.sha256((run / item["text_file"]).read_bytes()).hexdigest())
                self.assertEqual(item["row_url"], selected[item["step"]].prompt.url)
                self.assertTrue(item["version_id"].startswith(f"{FACTORY}/{item['step']}/v"))
            self.assertEqual(data["prompts"][0]["selected_by"], "override")

    def test_recorded_body_text(self):
        text = select_prompts(recorded())["judge"].prompt.text
        self.assertEqual(
            text,
            "Fixture prompt for step judge, version 1 (production).\n"
            "Recorded test data only; not a production prompt.\n"
            "Input: {evidence}\nOutput: JSON",
        )


if __name__ == "__main__":
    unittest.main()
