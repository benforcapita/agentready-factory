import contextlib
from datetime import datetime, timezone
import hashlib
import inspect
import io
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

import yaml

import arf.publish as publish
from arf.notion.schemas import ARTIFACTS
from arf.orchestrator import Orchestrator
from arf.publish import (
    ARTIFACT_FILES,
    NOTHING_SENT,
    REPORTS_FOLDER,
    MemoryDrive,
    Publisher,
    PublishingSteps,
    PublishStore,
    result_comment,
)
from arf.publish.__main__ import main
from arf.publish.replay import evidence_bundle
from arf.render import draft_comment
from arf.request import Request

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "fixtures/requests/sample.yaml"
GOLDEN = ROOT / "fixtures/golden/lehamim"
ACK = ROOT / "fixtures/lint/lehamim/lint-ack.json"
PUBLISH_SOURCES = sorted((ROOT / "src/arf/publish").glob("*.py"))
NOW = datetime(2026, 11, 22, 13, 30, tzinfo=timezone.utc)
FORBIDDEN = re.compile(r"permission|shar(e|ing)|anyone|domain.?wide|role|transfer|owner|notify|send|mail|post\(",
                       re.IGNORECASE)


def request(**changes):
    data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    data.update(changes)
    return Request.model_validate(data)


class FileSteps:
    """Writes a small draft set for each run; `passed` drives the lint result."""

    def __init__(self, out, passed=True):
        self.out = Path(out)
        self.passed = passed

    def directory(self, context):
        return self.out / context.run.id

    def collect(self, context):
        self.directory(context).mkdir(parents=True)

    def draft(self, context, fix):
        (self.directory(context) / "report.md").write_text(draft_comment(context.run.id) + "\n\n# דוח\n",
                                                           encoding="utf-8")

    def render(self, context):
        directory = self.directory(context)
        (directory / "report.html").write_text(draft_comment(context.run.id) + "<p>דוח</p>", encoding="utf-8")
        (directory / "notes-for-ben.md").write_text("notes\n", encoding="utf-8")

    def check(self, context):
        hard = 0 if self.passed else 1
        findings = [] if self.passed else [dict(rule="price-in-offer", level="hard_fail", acknowledged=False)]
        return dict(passed=self.passed, findings=findings, summary=dict(hard_fail=hard, warning=0))


class BrokenDrive(MemoryDrive):
    def upload(self, path, **kwargs):
        raise OSError("Drive unavailable")


class PublishTests(unittest.TestCase):
    def run_pipeline(self, out, *, mode="shadow", passed=True, drive=None):
        store, drive = PublishStore(), drive or MemoryDrive()
        steps = FileSteps(out, passed)
        publisher = Publisher(store, drive)
        orchestrator = Orchestrator(store, PublishingSteps(steps, publisher, steps.directory), clock=lambda: NOW)
        task = orchestrator.execute(orchestrator.submit(request(mode=mode)).task.id)
        return store, drive, publisher, task, store.runs[task.latest_run]

    def test_passing_run_uploads_privately_and_records_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, drive, publisher, task, run = self.run_pipeline(tmp)
            self.assertEqual((task.status, run.status), ("Needs review", "succeeded"))
            self.assertEqual([name for name, _ in drive.calls[:3]], ["find_folder", "create_folder", "create_folder"])
            reports, run_folder = drive.calls[1][1], drive.calls[2][1]
            self.assertEqual((reports["name"], reports["parent"]), (REPORTS_FOLDER, None))
            self.assertEqual(run_folder["name"], f"2026-11-22 example.com {run.id}")
            self.assertEqual({name for name, _ in drive.calls}, {"find_folder", "create_folder", "upload"})
            kinds = {a.kind: a for a in store.artifacts.values()}
            self.assertEqual(set(kinds), {"report_md", "report_html", "notes_for_ben", "lint_report", "manifest"})
            directory = Path(tmp) / run.id
            for artifact in store.artifacts.values():
                path = Path(artifact.box_path)
                self.assertEqual(path.parent, directory)
                self.assertEqual(artifact.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(artifact.size, path.stat().st_size)
                self.assertEqual(artifact.run, run.id)
                self.assertTrue(artifact.drive_link.startswith("https://drive.google.com/file/d/"))
                self.assertEqual(set(artifact.properties()), {p.name for p in ARTIFACTS.properties})
            self.assertTrue(kinds["report_md"].draft_marker_present)
            self.assertTrue(kinds["report_html"].draft_marker_present)
            self.assertEqual((kinds["report_md"].audience, kinds["notes_for_ben"].audience), ("client_draft", "private"))
            self.assertEqual(store.run_artifacts[run.id], tuple(store.artifacts))
            for record in drive.files.values():
                if not record["folder"]:
                    self.assertEqual(record["parent"], publisher.publications[run.id].folder["id"])

    def test_command_center_review_row_links_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, drive, publisher, task, run = self.run_pipeline(tmp)
            (row,) = store.command_center.values()
            self.assertEqual((row["Status"], row["Task"], row["Run"]), ("Needs review", task.id, run.id))
            self.assertEqual(row["Drive folder"], publisher.publications[run.id].folder["webViewLink"])
            self.assertEqual(store.task_links[task.id]["Command Center item"], row["url"])

    def test_eval_mode_skips_command_center(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, drive, publisher, task, run = self.run_pipeline(tmp, mode="eval")
            self.assertEqual(task.status, "Needs review")
            self.assertTrue(store.artifacts)
            self.assertEqual((store.command_center, store.task_links), ({}, {}))
            self.assertIn("eval run, no Command Center item", result_comment(task, run, publisher.publications[run.id]))

    def test_blocked_run_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, drive, publisher, task, run = self.run_pipeline(tmp, passed=False)
            self.assertEqual(task.status, "Guardrail blocked")
            self.assertEqual((drive.calls, store.artifacts, store.command_center), ([], {}, {}))
            comment = result_comment(task, run)
            self.assertIn("guardrail_blocked", comment)
            self.assertTrue(comment.rstrip().endswith(NOTHING_SENT))

    def test_upload_error_fails_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, drive, publisher, task, run = self.run_pipeline(tmp, drive=BrokenDrive())
            self.assertEqual((task.status, run.status), ("Failed", "failed"))
            self.assertIn("Drive unavailable", run.error)
            self.assertEqual((store.artifacts, store.command_center), ({}, {}))

    def test_existing_reports_folder_is_reused(self):
        drive = MemoryDrive()
        folder = drive.create_folder(REPORTS_FOLDER)
        with tempfile.TemporaryDirectory() as tmp:
            self.run_pipeline(tmp, drive=drive)
        self.assertEqual([c for c in drive.calls if c[0] == "create_folder" and c[1]["parent"] is None],
                         [("create_folder", dict(name=REPORTS_FOLDER, parent=None))])
        self.assertTrue(all(r["parent"] is not None for r in drive.files.values() if r["id"] != folder["id"]))

    def test_result_comment_lists_artifacts_and_states_nothing_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, drive, publisher, task, run = self.run_pipeline(tmp)
            comment = result_comment(task, run, publisher.publications[run.id])
        self.assertIn(NOTHING_SENT, comment)
        self.assertEqual(NOTHING_SENT, "Nothing sent; nobody contacted.")
        self.assertIn(f"{task.id} — Needs review", comment)
        for artifact in store.artifacts.values():
            self.assertIn(artifact.drive_link, comment)
        self.assertIn(store.task_links[task.id]["Command Center item"], comment)

    def test_artifact_files_cover_schema_kinds(self):
        kinds = next(p.options for p in ARTIFACTS.properties if p.name == "Kind")
        self.assertEqual(sorted(kind for _, kind, _, _ in ARTIFACT_FILES), sorted(kinds))

    def test_evidence_bundle_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "a.zip", Path(tmp) / "b.zip"
            evidence_bundle(GOLDEN, first)
            evidence_bundle(GOLDEN, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            names = zipfile.ZipFile(first).namelist()
        self.assertIn("probes.csv", names)
        self.assertNotIn("report.md", names)


class NoSideEffectApiTests(unittest.TestCase):
    def test_publish_code_has_no_sharing_or_permission_api(self):
        self.assertTrue(PUBLISH_SOURCES)
        for path in PUBLISH_SOURCES:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(FORBIDDEN.search(line), f"{path.name}:{number}: {line.strip()}")

    def test_drive_interface_is_folders_and_uploads_only(self):
        for drive in (publish.Drive, MemoryDrive):
            methods = {name for name, _ in inspect.getmembers(drive, inspect.isfunction) if not name.startswith("_")}
            self.assertEqual(methods, {"find_folder", "create_folder", "upload"})


class DryRunTests(unittest.TestCase):
    def dry_run(self, *args):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(output):
            code = main(["dry-run", str(SAMPLE), "--out", tmp, "--golden", str(GOLDEN), "--ack", str(ACK),
                         "--no-pdf", *args])
            files = sorted(p.name for p in Path(tmp).glob("run-0001/*"))
        return code, output.getvalue(), files

    def test_dry_run_reaches_needs_review_with_artifacts(self):
        code, output, files = self.dry_run()
        self.assertEqual(code, 0, output)
        self.assertIn("result: task task-0001 Needs review; run run-0001 succeeded; artifacts 7; command center 1",
                      output)
        self.assertIn("create Artifacts artifact-0007", output)
        self.assertIn(NOTHING_SENT, output)
        self.assertIn("result-comment.md", files)
        self.assertNotIn("report.pdf", files)

    def test_dry_run_eval_has_no_command_center_row(self):
        code, output, _ = self.dry_run("--eval")
        self.assertEqual(code, 0, output)
        self.assertIn("command center 0", output)

    def test_dry_run_forced_lint_failure_publishes_nothing(self):
        code, output, _ = self.dry_run("--force-lint-failure")
        self.assertEqual(code, 1)
        self.assertIn("artifacts 0; command center 0", output)
        self.assertNotIn("drive ", output)


if __name__ == "__main__":
    unittest.main()
