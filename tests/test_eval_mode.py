import contextlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from arf import cli
from arf.notion.schemas import RUNS
from arf.orchestrator import MemoryStore, Orchestrator
from arf.orchestrator.__main__ import main
from arf.orchestrator.eval import EvalMode, MockEvalDB
from arf.validation import validate_request

from .test_orchestrator import FakeSteps, NOW, ROOT, request

SKILL_EXAMPLE = ROOT / "skills/agentready-factory-request/request-template.yaml"
LEHAMIM = ROOT / "fixtures/requests/eval-lehamim.yaml"
CASE = "https://example.com/eval-cases/lehamim"
EVAL_RUN = "https://example.com/eval-runs/local"


class EvalModeTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.db = MockEvalDB(run_url=EVAL_RUN, cases=frozenset({CASE}))
        self.normal_steps = Mock()
        self.replay_steps = FakeSteps()
        self.factory = Orchestrator(
            self.store, self.normal_steps, clock=lambda: NOW,
            eval_mode=EvalMode(EVAL_RUN, self.replay_steps, self.db.record_result),
        )
        self.request = request(
            trigger="named_site", musy=None, mode="eval", eval_case=CASE, fixture_bundle="../golden/lehamim",
        )

    def test_eval_links_factory_run_and_result_without_normal_steps(self):
        submission = self.factory.submit(self.request)
        task = self.factory.execute(submission.task.id)
        run = self.store.runs[submission.run.id]
        (result,) = self.db.results
        self.assertIn("Eval run", {prop.name for prop in RUNS.properties})
        self.assertEqual(run.properties()["Eval run"], EVAL_RUN)
        self.assertEqual((result.factory_run_id, result.eval_run, result.eval_case), (run.id, EVAL_RUN, CASE))
        self.assertEqual((task.status, run.status, result.status), ("Needs review", "succeeded", "succeeded"))
        self.assertEqual(result.lint_hard_fails, 0)
        self.assertEqual(self.normal_steps.mock_calls, [])
        self.assertEqual({db for _, db, _, _ in self.store.log}, {"Tasks", "Runs"})

        duplicate = self.factory.submit(self.request)
        self.assertTrue(duplicate.reused)
        self.assertEqual(duplicate.run.id, run.id)
        self.assertEqual(len(self.db.results), 1)

    def test_eval_never_reuses_a_shadow_run(self):
        shadow = self.factory.submit(request(trigger="named_site", musy=None))
        evaluated = self.factory.submit(self.request)
        self.assertNotEqual(shadow.run.id, evaluated.run.id)
        self.assertEqual(evaluated.run.mode, "eval")
        self.assertIsNone(shadow.run.properties()["Eval run"])
        self.assertEqual(self.factory.submit(request(trigger="named_site", musy=None)).run.id, shadow.run.id)

    def test_cases_and_eval_runs_do_not_deduplicate_against_one_another(self):
        first = self.factory.submit(self.request)
        second = self.factory.submit(self.request.model_copy(update={"eval_case": CASE + "-other"}))
        third_factory = Orchestrator(
            self.store, self.normal_steps, clock=lambda: NOW,
            eval_mode=EvalMode(EVAL_RUN + "-other", self.replay_steps, self.db.record_result),
        )
        third = third_factory.submit(self.request)
        self.assertEqual(len({first.run.id, second.run.id, third.run.id}), 3)

    def test_eval_request_without_hook_fails_before_writes(self):
        factory = Orchestrator(self.store, self.normal_steps)
        with self.assertRaisesRegex(ValueError, "eval mode"):
            factory.submit(self.request)
        self.assertEqual(self.store.log, [])
        self.assertEqual(self.normal_steps.mock_calls, [])

    def test_blocked_eval_records_only_one_result_and_retry_has_its_own(self):
        self.replay_steps.results = [False]
        first = self.factory.submit(self.request)
        self.factory.execute(first.task.id)
        (result,) = self.db.results
        self.assertEqual((result.status, result.fix_attempts), ("guardrail_blocked", 2))

        self.replay_steps.results = [True]
        retry = self.factory.submit(self.request.model_copy(update={"force": True}))
        self.factory.execute(retry.task.id)
        self.assertEqual(retry.run.retry_of, first.run.id)
        self.assertEqual(self.store.runs[retry.run.id].properties()["Eval run"], EVAL_RUN)
        self.assertEqual(
            [(result.factory_run_id, result.status) for result in self.db.results],
            [(first.run.id, "guardrail_blocked"), (retry.run.id, "succeeded")],
        )

    def test_replay_failure_records_failed_eval(self):
        self.replay_steps.fail_at = "draft"
        submission = self.factory.submit(self.request)
        task = self.factory.execute(submission.task.id)
        (result,) = self.db.results
        self.assertEqual((task.status, result.status), ("Failed", "failed"))
        self.assertEqual(self.normal_steps.mock_calls, [])

    def test_result_write_failure_fails_run_without_retrying_write(self):
        writer = Mock(side_effect=RuntimeError("database unavailable"))
        factory = Orchestrator(
            self.store, self.normal_steps, eval_mode=EvalMode(EVAL_RUN, self.replay_steps, writer),
        )
        submission = factory.submit(self.request)
        task = factory.execute(submission.task.id)
        run = self.store.runs[submission.run.id]
        self.assertEqual((task.status, run.status), ("Failed", "failed"))
        self.assertIn("eval result write failed", run.error)
        writer.assert_called_once()


class RequesterAndDryRunTests(unittest.TestCase):
    def test_arf_validate_accepts_requester_template(self):
        with patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["validate", str(SKILL_EXAMPLE)]), 0)
            evaluated, _ = validate_request(LEHAMIM)
        self.assertEqual((evaluated.mode, str(evaluated.eval_case)), ("eval", CASE))

    def test_eval_disallows_public_scans_even_with_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "eval.yaml"
            bundle = (LEHAMIM.parent / "../golden/lehamim").resolve()
            source.write_text(
                LEHAMIM.read_text(encoding="utf-8").replace("../golden/lehamim", str(bundle))
                + "allow_public_scan: true\nben_ok: 'Ben approved at 2026-11-23T10:00:00+02:00'\n",
                encoding="utf-8",
            )
            with patch("socket.getaddrinfo") as dns:
                with self.assertRaisesRegex(ValueError, "public scans are not allowed in eval mode"):
                    validate_request(source)
                dns.assert_not_called()

    def test_lehamim_cli_writes_a_mock_result_without_network_or_review_row(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as log:
            with patch("socket.getaddrinfo", side_effect=AssertionError("unexpected DNS")), patch(
                "socket.create_connection", side_effect=AssertionError("unexpected network"),
            ):
                code = main(["dry-run", str(LEHAMIM), "--out", tmp, "--duplicate",
                             "--golden", str(Path(tmp) / "not-the-eval-bundle")])
            self.assertEqual(code, 0, log.getvalue())
            self.assertTrue((Path(tmp) / "run-0001/report.html").is_file())
            self.assertIn(f"Eval run={EVAL_RUN}", log.getvalue())
            self.assertIn("duplicate: reused=True", log.getvalue())
            self.assertNotIn("create Command Center", log.getvalue())
            (line,) = [line for line in log.getvalue().splitlines() if line.startswith("mock Eval Result: ")]
            row = json.loads(line.removeprefix("mock Eval Result: "))
            self.assertEqual(
                (row["factory_run_id"], row["eval_case"], row["eval_run"], row["status"]),
                ("run-0001", CASE, EVAL_RUN, "succeeded"),
            )
