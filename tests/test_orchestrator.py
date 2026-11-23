import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from arf.notion.schemas import RUNS, TASK_STATUSES, TASKS
from arf.orchestrator import (
    FINISHED_RUN_STATUSES,
    RUN_STATUSES,
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    IllegalTransition,
    ImmutableRun,
    MemoryStore,
    RUNTIME_FAKE_IP_POLICY,
    CollectingSteps,
    Orchestrator,
    check_transition,
    idempotency_key,
)
from arf.orchestrator.__main__ import main
from arf.orchestrator.replay import ReplaySteps
from arf.request import Request

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "fixtures/requests/sample.yaml"
GOLDEN = ROOT / "fixtures/golden/lehamim"
ACK = ROOT / "fixtures/lint/lehamim/lint-ack.json"
NOW = datetime(2026, 11, 22, 13, 30, tzinfo=timezone.utc)
HAPPY_PATH = ["Requested", "Claimed", "Collecting evidence", "Drafting", "Rendering", "Checking", "Needs review"]


def request(**changes):
    data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    data.update(changes)
    return Request.model_validate(data)


def lint_result(passed):
    findings = [] if passed else [dict(rule="price-in-offer", level="hard_fail", acknowledged=False)]
    return dict(passed=passed, findings=findings, summary=dict(hard_fail=0 if passed else 1, warning=0))


class FakeSteps:
    def __init__(self, results=(True,), fail_at=None):
        self.results = list(results)
        self.fail_at = fail_at
        self.calls = []

    def _call(self, name, context):
        self.calls.append((name, context.attempt))
        if name == self.fail_at:
            raise RuntimeError(f"{name} broke")

    def collect(self, context):
        self._call("collect", context)

    def draft(self, context, fix):
        self._call("draft", context)
        self.calls.append(("fix", [f["rule"] for f in fix] if fix else None))

    def render(self, context):
        self._call("render", context)

    def check(self, context):
        self._call("check", context)
        return lint_result(self.results.pop(0) if len(self.results) > 1 else self.results[0])


def statuses(store, database, record_id):
    return [changes["Status"] for op, db, rid, changes in store.log if db == database and rid == record_id
            and "Status" in changes]


class TransitionTests(unittest.TestCase):
    def test_tables_cover_every_schema_status(self):
        self.assertEqual(set(TASK_TRANSITIONS), set(TASK_STATUSES))
        self.assertEqual(set(RUN_TRANSITIONS), set(RUN_STATUSES))
        for targets in [*TASK_TRANSITIONS.values(), *RUN_TRANSITIONS.values()]:
            self.assertLessEqual(targets, set(TASK_STATUSES) | set(RUN_STATUSES))

    def test_happy_path_is_legal(self):
        for current, new in zip(HAPPY_PATH, HAPPY_PATH[1:]):
            check_transition("task", current, new)
        for current, new in (("queued", "running"), ("running", "succeeded")):
            check_transition("run", current, new)

    def test_illegal_transitions(self):
        for kind, current, new in (
            ("task", "Requested", "Drafting"),
            ("task", "Requested", "Needs review"),
            ("task", "Claimed", "Needs review"),
            ("task", "Drafting", "Needs review"),
            ("task", "Needs review", "Drafting"),
            ("task", "Accepted", "Requested"),
            ("task", "Duplicate", "Claimed"),
            ("task", "Cancelled", "Requested"),
            ("task", "Failed", "Requested"),
            ("task", "Guardrail blocked", "Requested"),
            ("task", "Claimed", "Guardrail blocked"),
            ("task", "Requested", "Done"),
            ("run", "queued", "succeeded"),
            ("run", "running", "queued"),
            ("run", "succeeded", "running"),
            ("run", "guardrail_blocked", "queued"),
            ("run", "failed", "running"),
        ):
            with self.subTest(kind=kind, current=current, new=new):
                with self.assertRaises(IllegalTransition):
                    check_transition(kind, current, new)

    def test_terminal_states(self):
        for status in ("Accepted", "Accepted with edits", "Rejected", "Cancelled", "Rejected request", "Duplicate"):
            self.assertEqual(TASK_TRANSITIONS[status], frozenset())
        for status in FINISHED_RUN_STATUSES:
            self.assertEqual(RUN_TRANSITIONS[status], frozenset())

    def test_store_rejects_illegal_task_write(self):
        store = MemoryStore()
        orchestrator = Orchestrator(store, FakeSteps(), clock=lambda: NOW)
        task = orchestrator.submit(request()).task
        with self.assertRaises(IllegalTransition):
            store.update_task(task.id, status="Needs review")
        self.assertEqual(store.tasks[task.id].status, "Requested")


class IdempotencyTests(unittest.TestCase):
    def test_key_fields(self):
        self.assertEqual(idempotency_key(request(), NOW), "example.com|2026-11-22|musy_lead|10001")
        named = request(trigger="named_site", musy=None, site="https://WWW.Example.com./path")
        self.assertEqual(idempotency_key(named, NOW), "example.com|2026-11-22|named_site|-")

    def test_key_uses_israel_date(self):
        late_utc = datetime(2026, 11, 22, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(idempotency_key(request(), late_utc).split("|")[1], "2026-11-23")
        self.assertNotEqual(idempotency_key(request(), NOW), idempotency_key(request(), NOW + timedelta(days=1)))
        with self.assertRaises(ValueError):
            idempotency_key(request(), NOW.replace(tzinfo=None))

    def test_key_changes_with_message_and_trigger(self):
        base = idempotency_key(request(), NOW)
        self.assertNotEqual(base, idempotency_key(request(musy={**request().musy.model_dump(mode="json"),
                                                                  "message_id": "10002"}), NOW))
        self.assertNotEqual(base, idempotency_key(request(site="https://other.example"), NOW))

    def test_duplicate_reuses_run(self):
        store = MemoryStore()
        orchestrator = Orchestrator(store, FakeSteps(), clock=lambda: NOW)
        first = orchestrator.submit(request())
        orchestrator.execute(first.task.id)
        again = orchestrator.submit(request())
        self.assertTrue(again.reused)
        self.assertEqual(again.run.id, first.run.id)
        self.assertEqual(again.task.id, first.task.id)
        self.assertEqual(len(store.runs), 1)
        duplicate = store.tasks["task-0002"]
        self.assertEqual((duplicate.status, duplicate.latest_run, duplicate.duplicate_of), ("Duplicate", "run-0001", "task-0001"))
        self.assertEqual(store.find_task(first.task.idempotency_key).id, first.task.id)

    def test_duplicate_while_in_flight_reuses_queued_run(self):
        store = MemoryStore()
        orchestrator = Orchestrator(store, FakeSteps(), clock=lambda: NOW)
        first = orchestrator.submit(request())
        again = orchestrator.submit(request())
        self.assertEqual((again.reused, again.run.id, again.run.status), (True, first.run.id, "queued"))

    def test_force_starts_a_new_run(self):
        store = MemoryStore()
        orchestrator = Orchestrator(store, FakeSteps(), clock=lambda: NOW)
        first = orchestrator.submit(request())
        orchestrator.execute(first.task.id)
        forced = orchestrator.submit(request(force=True))
        self.assertFalse(forced.reused)
        self.assertNotEqual(forced.run.id, first.run.id)
        self.assertNotEqual(forced.task.id, first.task.id)
        self.assertIsNone(forced.run.retry_of)
        self.assertEqual(orchestrator.execute(forced.task.id).status, "Needs review")
        self.assertEqual(store.find_task(first.task.idempotency_key).id, forced.task.id)

    def test_closed_request_is_not_reused(self):
        store = MemoryStore()
        orchestrator = Orchestrator(store, FakeSteps(), clock=lambda: NOW)
        first = orchestrator.submit(request())
        orchestrator.cancel(first.task.id)
        again = orchestrator.submit(request())
        self.assertFalse(again.reused)
        self.assertNotEqual(again.run.id, first.run.id)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()

    def orchestrator(self, steps):
        return Orchestrator(self.store, steps, clock=lambda: NOW)

    def test_walks_requested_to_needs_review(self):
        orchestrator = self.orchestrator(FakeSteps())
        submission = orchestrator.submit(request())
        task = orchestrator.execute(submission.task.id)
        self.assertEqual(task.status, "Needs review")
        self.assertEqual(statuses(self.store, TASKS.title, task.id), HAPPY_PATH)
        self.assertEqual(statuses(self.store, RUNS.title, submission.run.id), ["queued", "running", "succeeded"])
        run = self.store.runs[submission.run.id]
        self.assertEqual((run.lint_hard_fails, run.fix_attempts, run.finished), (0, 0, NOW))

    def test_fix_loop_recovers(self):
        steps = FakeSteps([False, True])
        orchestrator = self.orchestrator(steps)
        submission = orchestrator.submit(request())
        self.assertEqual(orchestrator.execute(submission.task.id).status, "Needs review")
        self.assertEqual(self.store.runs[submission.run.id].fix_attempts, 1)
        self.assertIn(("fix", ["price-in-offer"]), steps.calls)

    def test_guardrail_blocked_after_two_fix_attempts(self):
        steps = FakeSteps([False])
        orchestrator = self.orchestrator(steps)
        submission = orchestrator.submit(request())
        task = orchestrator.execute(submission.task.id)
        run = self.store.runs[submission.run.id]
        self.assertEqual(task.status, "Guardrail blocked")
        self.assertEqual((run.status, run.fix_attempts), ("guardrail_blocked", 2))
        self.assertIn("price-in-offer", run.error)
        self.assertEqual([attempt for name, attempt in steps.calls if name == "draft"], [0, 1, 2])
        self.assertEqual(statuses(self.store, TASKS.title, task.id).count("Drafting"), 3)

    def test_step_error_fails_run(self):
        orchestrator = self.orchestrator(FakeSteps(fail_at="render"))
        submission = orchestrator.submit(request())
        task = orchestrator.execute(submission.task.id)
        run = self.store.runs[submission.run.id]
        self.assertEqual((task.status, run.status), ("Failed", "failed"))
        self.assertEqual(run.error, "RuntimeError: render broke")

    def test_finished_run_is_immutable(self):
        orchestrator = self.orchestrator(FakeSteps())
        submission = orchestrator.submit(request())
        orchestrator.execute(submission.task.id)
        for changes in (dict(status="running"), dict(error="late"), dict(lint_warnings=3)):
            with self.subTest(changes=changes), self.assertRaises(ImmutableRun):
                self.store.update_run(submission.run.id, **changes)
        with self.assertRaises(IllegalTransition):
            orchestrator.execute(submission.task.id)
        with self.assertRaises(Exception):
            self.store.runs[submission.run.id].status = "running"

    def test_duplicate_of_blocked_task_needs_force(self):
        orchestrator = self.orchestrator(FakeSteps([False]))
        first = orchestrator.submit(request())
        orchestrator.execute(first.task.id)
        for _ in range(5):
            again = orchestrator.submit(request())
            self.assertEqual((again.needs_force, again.retried, again.reused), (True, False, False))
            self.assertEqual((again.task.id, again.run.id), (first.task.id, first.run.id))
        self.assertEqual(list(self.store.runs), [first.run.id])
        self.assertEqual(self.store.tasks[first.task.id].status, "Guardrail blocked")
        duplicates = [t for t in self.store.tasks.values() if t.duplicate_of == first.task.id]
        self.assertEqual({t.status for t in duplicates}, {"Duplicate"})
        self.assertEqual(len(duplicates), 5)

    def test_force_retries_blocked_task_with_retry_of(self):
        steps = FakeSteps([False])
        orchestrator = self.orchestrator(steps)
        first = orchestrator.submit(request())
        orchestrator.execute(first.task.id)
        steps.results = [True]
        retry = orchestrator.submit(request(force=True))
        self.assertTrue(retry.retried)
        self.assertEqual((retry.task.id, retry.run.retry_of, retry.task.status), (first.task.id, first.run.id, "Claimed"))
        self.assertEqual(orchestrator.execute(retry.task.id).status, "Needs review")
        task = self.store.tasks[first.task.id]
        self.assertEqual((task.runs, task.latest_run), ((first.run.id, retry.run.id), retry.run.id))
        self.assertEqual(len(self.store.tasks), 1)
        history = statuses(self.store, TASKS.title, task.id)
        self.assertEqual(history[history.index("Guardrail blocked"):], ["Guardrail blocked", *HAPPY_PATH[1:]])
        self.assertEqual(self.store.runs[first.run.id].status, "guardrail_blocked")
        self.assertEqual(self.store.runs[retry.run.id].properties()["Retry of"], [first.run.id])
        self.assertEqual(statuses(self.store, RUNS.title, retry.run.id), ["queued", "running", "succeeded"])

    def test_force_retries_failed_task(self):
        steps = FakeSteps(fail_at="collect")
        orchestrator = self.orchestrator(steps)
        first = orchestrator.submit(request())
        self.assertEqual(orchestrator.execute(first.task.id).status, "Failed")
        self.assertTrue(orchestrator.submit(request()).needs_force)
        steps.fail_at = None
        retry = orchestrator.submit(request(force=True))
        self.assertEqual((retry.task.id, retry.task.status, retry.run.retry_of), (first.task.id, "Claimed", first.run.id))
        self.assertEqual(orchestrator.execute(retry.task.id).status, "Needs review")

    def test_retry_requires_failed_task(self):
        orchestrator = self.orchestrator(FakeSteps())
        submission = orchestrator.submit(request())
        with self.assertRaises(IllegalTransition):
            orchestrator.retry(submission.task.id)

    def test_cancel(self):
        orchestrator = self.orchestrator(FakeSteps())
        submission = orchestrator.submit(request())
        orchestrator.cancel(submission.task.id)
        self.assertEqual(self.store.runs[submission.run.id].status, "cancelled")
        with self.assertRaises(IllegalTransition):
            orchestrator.execute(submission.task.id)

    def test_cancel_duplicate_writes_nothing(self):
        orchestrator = self.orchestrator(FakeSteps())
        first = orchestrator.submit(request())
        orchestrator.submit(request())
        duplicate = next(t for t in self.store.tasks.values() if t.duplicate_of == first.task.id)
        writes = len(self.store.log)
        with self.assertRaises(IllegalTransition):
            orchestrator.cancel(duplicate.id)
        self.assertEqual(len(self.store.log), writes)
        self.assertEqual(self.store.tasks[first.task.id].status, "Requested")
        self.assertEqual(self.store.runs[first.run.id].status, "queued")
        self.assertEqual(orchestrator.execute(first.task.id).status, "Needs review")
        again = orchestrator.submit(request())
        self.assertEqual((again.reused, again.run.status), (True, "succeeded"))

    def test_stop_states_are_not_cancellable(self):
        for steps in (FakeSteps([False]), FakeSteps(fail_at="collect")):
            store = MemoryStore()
            orchestrator = Orchestrator(store, steps, clock=lambda: NOW)
            submission = orchestrator.submit(request())
            task = orchestrator.execute(submission.task.id)
            writes = len(store.log)
            with self.subTest(status=task.status), self.assertRaises(IllegalTransition):
                orchestrator.cancel(task.id)
            self.assertEqual(len(store.log), writes)
            self.assertEqual(store.tasks[task.id].status, task.status)

    def test_forced_retry_uses_forced_request_and_task_mode(self):
        seen = []

        class Recording(FakeSteps):
            def collect(self, context):
                seen.append((context.request.force, context.request.mode))
                super().collect(context)

        steps = Recording([False])
        orchestrator = self.orchestrator(steps)
        first = orchestrator.submit(request())
        orchestrator.execute(first.task.id)
        steps.results = [True]
        retry = orchestrator.submit(request(force=True, mode="reviewed"))
        self.assertEqual(retry.run.mode, "shadow")
        orchestrator.execute(retry.task.id)
        self.assertEqual(seen, [(False, "shadow"), (True, "reviewed")])

    def test_writes_match_notion_schema(self):
        orchestrator = self.orchestrator(FakeSteps([False]))
        submission = orchestrator.submit(request())
        orchestrator.execute(submission.task.id)
        orchestrator.retry(submission.task.id)
        schemas = {TASKS.title: TASKS, RUNS.title: RUNS}
        for op, database, record_id, changes in self.store.log:
            properties = {p.name: p for p in schemas[database].properties}
            for name, value in changes.items():
                with self.subTest(database=database, name=name):
                    self.assertIn(name, properties)
                    if properties[name].options and value is not None:
                        self.assertIn(value, properties[name].options)


class ReplayTests(unittest.TestCase):
    fixture_args = ("--golden", str(GOLDEN), "--ack", str(ACK))

    def run_replay(self, failing_drafts):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = MemoryStore()
        orchestrator = Orchestrator(store, ReplaySteps(Path(directory.name), failing_drafts=failing_drafts,
                                                            golden=GOLDEN, ack=ACK),
                                    clock=lambda: NOW)
        submission = orchestrator.submit(request())
        return store, orchestrator.execute(submission.task.id), Path(directory.name) / submission.run.id

    def test_recorded_draft_reaches_needs_review(self):
        store, task, directory = self.run_replay(0)
        self.assertEqual(task.status, "Needs review")
        self.assertEqual(statuses(store, TASKS.title, task.id), HAPPY_PATH)
        self.assertIn("arf:draft run=run-0001", (directory / "report.md").read_text(encoding="utf-8"))
        self.assertTrue((directory / "report.html").is_file())

    def test_recorded_fix_recovers(self):
        store, task, _ = self.run_replay(1)
        self.assertEqual((task.status, store.runs[task.latest_run].fix_attempts), ("Needs review", 1))

    def test_forced_lint_failure_blocks(self):
        store, task, _ = self.run_replay(3)
        run = store.runs[task.latest_run]
        self.assertEqual((task.status, run.status, run.fix_attempts), ("Guardrail blocked", "guardrail_blocked", 2))
        self.assertGreater(run.lint_hard_fails, 0)
        self.assertIn("price-in-offer", run.error)

    def test_cli_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["dry-run", str(SAMPLE), "--out", directory, "--duplicate", *self.fixture_args])
            self.assertEqual(code, 0, output.getvalue())
            self.assertIn("duplicate: reused=True run=run-0001", output.getvalue())
            self.assertIn("result: task task-0001 Needs review; run run-0001 succeeded", output.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["dry-run", str(SAMPLE), "--out", directory, "--force-lint-failure", *self.fixture_args])
            self.assertEqual(code, 1)
            self.assertIn("Guardrail blocked; run run-0001 guardrail_blocked; fix attempts 2", output.getvalue())


class RuntimeFakeIPPolicyTests(unittest.TestCase):
    def run_collecting(self, environ):
        collector = Mock(return_value={"requests_made": 0})
        reader = Mock(return_value={"paths": ["/"]})
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", environ, clear=True):
            steps = FakeSteps()
            store = MemoryStore()
            orchestrator = Orchestrator(store, CollectingSteps(steps, lambda context: Path(directory) / context.run.id,
                                                               collector=collector, reader=reader), clock=lambda: NOW)
            submission = orchestrator.submit(request())
            with self.assertLogs("arf.orchestrator", "INFO") as logs:
                task = orchestrator.execute(submission.task.id)
            self.assertEqual(task.status, "Needs review")
            self.assertEqual(steps.calls[0], ("collect", 0))
            collector.assert_called_once_with(request().site, Path(directory) / submission.run.id / "evidence",
                                              fake_ip_policy=collector.call_args.kwargs["fake_ip_policy"])
        return collector.call_args.kwargs["fake_ip_policy"], logs.output

    def test_orchestrator_passes_doh_verify_to_collect(self):
        policy, logs = self.run_collecting({})
        self.assertEqual((RUNTIME_FAKE_IP_POLICY, policy), ("doh_verify", "doh_verify"))
        self.assertIn("fake_ip_policy=doh_verify (factory runtime default)", logs[0])

    def test_explicit_environment_policy_wins(self):
        for value in ("refuse", "doh_verify"):
            with self.subTest(value=value):
                policy, logs = self.run_collecting({"ARF_FAKE_IP_POLICY": value})
                self.assertEqual(policy, value)
                self.assertIn(f"fake_ip_policy={value} (ARF_FAKE_IP_POLICY)", logs[0])

    def test_library_default_stays_refuse(self):
        from arf.collect import config
        self.assertEqual(config.FAKE_IP_POLICY, "refuse")

    def test_collect_failure_fails_the_run(self):
        collector = Mock(side_effect=OSError("collection completed with 1 error(s); see errors.json"))
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore()
            orchestrator = Orchestrator(store, CollectingSteps(FakeSteps(), lambda context: Path(directory),
                                                               collector=collector, reader=Mock()), clock=lambda: NOW)
            task = orchestrator.execute(orchestrator.submit(request()).task.id)
        self.assertEqual(task.status, "Failed")
        self.assertIn("errors.json", store.runs[task.latest_run].error)


class ReaderWiringTests(unittest.TestCase):
    def test_article_paths_reach_read_site(self):
        def collector(site, out, **_):
            out.mkdir(parents=True)
            (out / "articles.json").write_text(
                json.dumps({"articles": [{"path": "/a"}, {"path": "/b"}],
                            "paths": ["/a", "/b"], "reason": None}) + "\n", encoding="utf-8")
            return {"requests_made": 0}

        reader = Mock(return_value={"jina": [], "tinyfish": {}})
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore()
            orchestrator = Orchestrator(store, CollectingSteps(FakeSteps(), lambda context: Path(directory),
                                                               collector=collector, reader=reader), clock=lambda: NOW)
            task = orchestrator.execute(orchestrator.submit(request()).task.id)
            self.assertEqual(task.status, "Needs review")
            reader.assert_called_once_with(
                request().site, ["/", "/a", "/b"], Path(directory) / "evidence" / "readers",
                jina_key=None, tinyfish_key=None)

    def test_no_articles_reads_home_only(self):
        reader = Mock(return_value={})
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore()
            orchestrator = Orchestrator(store, CollectingSteps(
                FakeSteps(), lambda context: Path(directory),
                collector=Mock(return_value={}), reader=reader), clock=lambda: NOW)
            task = orchestrator.execute(orchestrator.submit(request()).task.id)
            self.assertEqual(task.status, "Needs review")
            self.assertEqual(reader.call_args.args[1], ["/"])


if __name__ == "__main__":
    unittest.main()
