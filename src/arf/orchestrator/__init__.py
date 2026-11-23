"""Task and Run state machines from LLD §4 with idempotency, retries and the guardrail fix loop.

T8a only orchestrates: it records Task/Run state through a store and calls pluggable steps.
Publishing (Drive upload, Artifacts rows, Command Center row, result comment) is T8b.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ..notion.schemas import RUNS, TASK_STATUSES, TASKS
from .eval import EvalMode, EvalResult

ISRAEL = ZoneInfo("Asia/Jerusalem")
MAX_FIX_ATTEMPTS = 2
RUNTIME_FAKE_IP_POLICY = "doh_verify"

log = logging.getLogger(__name__)

RUN_STATUSES = next(p.options for p in RUNS.properties if p.name == "Status")
FINISHED_RUN_STATUSES = frozenset({"succeeded", "guardrail_blocked", "failed", "cancelled"})

_IN_FLIGHT = ("Claimed", "Collecting evidence", "Drafting", "Rendering", "Checking")
_STOP = ("Failed", "Cancelled")

TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "Requested": frozenset({"Claimed", "Rejected request", "Duplicate", "Cancelled"}),
    "Claimed": frozenset({"Collecting evidence", *_STOP}),
    "Collecting evidence": frozenset({"Drafting", *_STOP}),
    "Drafting": frozenset({"Rendering", *_STOP}),
    "Rendering": frozenset({"Checking", *_STOP}),
    "Checking": frozenset({"Needs review", "Drafting", "Guardrail blocked", *_STOP}),
    "Needs review": frozenset({"Accepted", "Accepted with edits", "Rejected", "Cancelled"}),
    "Failed": frozenset({"Claimed"}),
    "Guardrail blocked": frozenset({"Claimed"}),
    "Accepted": frozenset(),
    "Accepted with edits": frozenset(),
    "Rejected": frozenset(),
    "Cancelled": frozenset(),
    "Rejected request": frozenset(),
    "Duplicate": frozenset(),
}

RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "guardrail_blocked", "failed", "cancelled"}),
    **{status: frozenset() for status in FINISHED_RUN_STATUSES},
}

RETRYABLE_TASK_STATUSES = frozenset({"Failed", "Guardrail blocked"})
CLOSED_REQUEST_STATUSES = frozenset({"Cancelled", "Rejected request", "Duplicate"})

assert set(TASK_TRANSITIONS) == set(TASK_STATUSES)
assert set(RUN_TRANSITIONS) == set(RUN_STATUSES)


class IllegalTransition(ValueError):
    pass


class ImmutableRun(ValueError):
    pass


def check_transition(kind: str, current: str, new: str) -> None:
    table = TASK_TRANSITIONS if kind == "task" else RUN_TRANSITIONS
    if new not in table:
        raise IllegalTransition(f"unknown {kind} status {new!r}")
    if current not in table or new not in table[current]:
        raise IllegalTransition(f"illegal {kind} transition {current!r} -> {new!r}")


def request_domain(site: str) -> str:
    host = urlsplit(site.strip()).hostname
    if not host:
        raise ValueError(f"site has no host: {site!r}")
    host = host.encode("idna").decode("ascii").lower().removesuffix(".")
    return host.removeprefix("www.")


def idempotency_key(request: Any, now: datetime) -> str:
    """domain + Israel date + trigger + Musy message_id (PRO-403 design, LLD §4)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    message_id = request.musy.message_id if request.musy is not None else "-"
    israel_date = now.astimezone(ISRAEL).date().isoformat()
    return f"{request_domain(request.site)}|{israel_date}|{request.trigger}|{message_id}"


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if moment else None


@dataclass(frozen=True)
class Task:
    id: str
    status: str
    site: str
    domain: str
    trigger: str
    mode: str
    idempotency_key: str
    linear_ticket: str | None = None
    musy_message: str | None = None
    runs: tuple[str, ...] = ()
    latest_run: str | None = None
    duplicate_of: str | None = None

    def properties(self) -> dict[str, Any]:
        return {
            "Name": self.domain,
            "Status": self.status,
            "Site": self.site,
            "Domain": self.domain,
            "Trigger": self.trigger,
            "Mode": self.mode,
            "Linear ticket": self.linear_ticket,
            "Musy message": self.musy_message,
            "Idempotency key": self.idempotency_key,
            "Runs": list(self.runs),
            "Latest run": [self.latest_run] if self.latest_run else [],
        }


@dataclass(frozen=True)
class Run:
    id: str
    task: str
    status: str
    mode: str
    retry_of: str | None = None
    started: datetime | None = None
    finished: datetime | None = None
    lint_hard_fails: int | None = None
    lint_warnings: int | None = None
    fix_attempts: int = 0
    error: str | None = None
    eval_run: str | None = None

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_RUN_STATUSES

    def properties(self) -> dict[str, Any]:
        duration = (self.finished - self.started).total_seconds() if self.started and self.finished else None
        return {
            "Run ID": self.id,
            "Task": [self.task],
            "Status": self.status,
            "Mode": self.mode,
            "Started": _iso(self.started),
            "Finished": _iso(self.finished),
            "Duration (s)": duration,
            "Lint hard fails": self.lint_hard_fails,
            "Lint warnings": self.lint_warnings,
            "Error": self.error,
            "Retry of": [self.retry_of] if self.retry_of else [],
            "Eval run": self.eval_run,
        }


class MemoryStore:
    """In-process stand-in for the Notion Tasks and Runs databases; every write is logged.

    Records are frozen and finished Runs reject every further write (LLD §4: Runs are immutable
    once finished; a retry is a new Run linked through Retry of).
    """

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.runs: dict[str, Run] = {}
        self.log: list[tuple[str, str, str, dict[str, Any]]] = []

    def _write(self, op: str, database: str, record: Task | Run, changes: dict[str, Any]) -> None:
        if changes:
            self.log.append((op, database, record.id, changes))

    def create_task(self, **values: Any) -> Task:
        task = Task(id=f"task-{len(self.tasks) + 1:04}", **values)
        self.tasks[task.id] = task
        self._write("create", TASKS.title, task, task.properties())
        return task

    def create_run(self, **values: Any) -> Run:
        run = Run(id=f"run-{len(self.runs) + 1:04}", **values)
        self.runs[run.id] = run
        self._write("create", RUNS.title, run, run.properties())
        return run

    def update_task(self, task_id: str, **changes: Any) -> Task:
        old = self.tasks[task_id]
        if "status" in changes and changes["status"] != old.status:
            check_transition("task", old.status, changes["status"])
        new = replace(old, **changes)
        self.tasks[task_id] = new
        self._write("update", TASKS.title, new, _diff(old.properties(), new.properties()))
        return new

    def update_run(self, run_id: str, **changes: Any) -> Run:
        old = self.runs[run_id]
        if old.is_finished:
            raise ImmutableRun(f"{run_id} is {old.status}; finished Runs are immutable")
        if "status" in changes and changes["status"] != old.status:
            check_transition("run", old.status, changes["status"])
        new = replace(old, **changes)
        self.runs[run_id] = new
        self._write("update", RUNS.title, new, _diff(old.properties(), new.properties()))
        return new

    def find_task(self, key: str) -> Task | None:
        matches = [t for t in self.tasks.values() if t.idempotency_key == key and t.duplicate_of is None]
        return matches[-1] if matches else None

    def transcript(self) -> list[str]:
        lines = []
        for op, database, record_id, changes in self.log:
            values = ", ".join(f"{name}={value}" for name, value in changes.items() if value not in (None, []))
            lines.append(f"{op} {database} {record_id}: {values}")
        return lines


def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in new.items() if old.get(name) != value}


@dataclass
class RunContext:
    request: Any
    task: Task
    run: Run
    attempt: int = 0
    state: dict[str, Any] = field(default_factory=dict)


class Steps(Protocol):
    def collect(self, context: RunContext) -> None: ...

    def draft(self, context: RunContext, fix: list[dict[str, Any]] | None) -> None: ...

    def render(self, context: RunContext) -> None: ...

    def check(self, context: RunContext) -> dict[str, Any]: ...


def runtime_fake_ip_policy() -> str:
    """The factory runtime's fake-IP policy: an explicit ARF_FAKE_IP_POLICY wins, else doh_verify.

    The library default (`arf.collect.config.FAKE_IP_POLICY`) stays `refuse` for bare `arf collect`.
    """
    return os.environ.get("ARF_FAKE_IP_POLICY", RUNTIME_FAKE_IP_POLICY)


class CollectingSteps:
    """Wraps run steps so the collect step gathers evidence with the factory runtime fake-IP policy.

    The evidence bundle goes to `directory(context) / "evidence"` after the wrapped collect step runs.
    """

    def __init__(self, steps: Steps, directory: Callable[[RunContext], Any], *,
                 collector: Callable[..., dict[str, Any]] | None = None) -> None:
        self.steps = steps
        self.directory = directory
        self.collector = collector

    def collect(self, context: RunContext) -> None:
        self.steps.collect(context)
        collector = self.collector
        if collector is None:
            from ..collect import collect as collector
        policy = runtime_fake_ip_policy()
        source = "ARF_FAKE_IP_POLICY" if "ARF_FAKE_IP_POLICY" in os.environ else "factory runtime default"
        log.info("collect %s: fake_ip_policy=%s (%s)", context.request.site, policy, source)
        context.state["fake_ip_policy"] = policy
        context.state["evidence"] = collector(context.request.site, Path(self.directory(context)) / "evidence",
                                              fake_ip_policy=policy)

    def draft(self, context: RunContext, fix: list[dict[str, Any]] | None) -> None:
        self.steps.draft(context, fix)

    def render(self, context: RunContext) -> None:
        self.steps.render(context)

    def check(self, context: RunContext) -> dict[str, Any]:
        return self.steps.check(context)


@dataclass(frozen=True)
class Submission:
    task: Task
    run: Run
    reused: bool = False
    retried: bool = False
    needs_force: bool = False


class Orchestrator:
    def __init__(
        self,
        store: MemoryStore,
        steps: Steps,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_fix_attempts: int = MAX_FIX_ATTEMPTS,
        eval_mode: EvalMode | None = None,
    ) -> None:
        self.store = store
        self.steps = steps
        self.clock = clock
        self.max_fix_attempts = max_fix_attempts
        self.eval_mode = eval_mode
        self._requests: dict[str, Any] = {}

    def submit(self, request: Any) -> Submission:
        """Record a request as a Requested Task with a queued Run, or reuse the existing Task.

        LLD §4.1/§4.2/§7.2, by the latest Task with the same idempotency key:
        - Failed or Guardrail blocked: without `force: true` no Run starts; the request is
          recorded as Duplicate and `needs_force` asks for a resubmit with `force: true`.
          With `force: true` the same Task returns to Claimed with a new Run whose Retry of
          is the previous Run.
        - open (queued or running) or finished: the latest Run is reused and the request is
          recorded as Duplicate, unless `force: true` starts a fresh Task and Run.
        - Cancelled, Rejected request or none: a fresh Task and Run.
        """
        key = idempotency_key(request, self.clock())
        eval_run = None
        if request.mode == "eval":
            if request.allow_public_scan:
                raise ValueError("public scans are not allowed in eval mode")
            case = getattr(request, "eval_case", None)
            bundle = getattr(request, "fixture_bundle", None)
            # T8b's publish-only smoke test sets the mode without eval fields.
            # Unlike that synthetic request, a validated eval needs the harness.
            if case is not None or bundle is not None or self.eval_mode is not None:
                if not case or not bundle or self.eval_mode is None or not self.eval_mode.run_url:
                    raise ValueError("eval mode requires an eval case, fixture bundle and Eval Run")
                eval_run = self.eval_mode.run_url
                key = "eval:" + json.dumps([key, eval_run, str(case)])
        existing = self.store.find_task(key)
        if existing is not None and existing.status in RETRYABLE_TASK_STATUSES:
            if request.force:
                self._requests[existing.id] = request
                return self.retry(existing.id)
            self._record_duplicate(request, key, existing)
            return Submission(existing, self.store.runs[existing.latest_run], needs_force=True)
        if existing is not None and not request.force and existing.status not in CLOSED_REQUEST_STATUSES:
            self._record_duplicate(request, key, existing)
            return Submission(existing, self.store.runs[existing.latest_run], reused=True)
        task = self.store.create_task(status="Requested", **self._task_values(request, key))
        run = self.store.create_run(task=task.id, status="queued", mode=request.mode, eval_run=eval_run)
        task = self.store.update_task(task.id, runs=(run.id,), latest_run=run.id)
        self._requests[task.id] = request
        return Submission(task, run)

    def _record_duplicate(self, request: Any, key: str, existing: Task) -> Task:
        duplicate = self.store.create_task(
            status="Requested", latest_run=existing.latest_run, duplicate_of=existing.id,
            **self._task_values(request, key),
        )
        return self.store.update_task(duplicate.id, status="Duplicate")

    def retry(self, task_id: str) -> Submission:
        """Return a Failed or Guardrail blocked Task to Claimed with a new Run (Retry of = previous)."""
        task = self.store.tasks[task_id]
        if task.status not in RETRYABLE_TASK_STATUSES:
            raise IllegalTransition(f"{task_id} is {task.status!r}; only Failed or Guardrail blocked Tasks retry")
        previous = self.store.runs[task.latest_run]
        run = self.store.create_run(
            task=task_id, status="queued", mode=task.mode, retry_of=previous.id, eval_run=previous.eval_run,
        )
        task = self.store.update_task(
            task_id, status="Claimed", runs=(*task.runs, run.id), latest_run=run.id,
        )
        return Submission(task, run, retried=True)

    def cancel(self, task_id: str) -> Task:
        """Cancel an active Task and its own open Run; illegal cancels write nothing."""
        task = self.store.tasks[task_id]
        check_transition("task", task.status, "Cancelled")
        run = self.store.runs.get(task.latest_run or "")
        if run is not None and run.task == task_id and not run.is_finished:
            self.store.update_run(run.id, status="cancelled", finished=self.clock())
        return self.store.update_task(task_id, status="Cancelled")

    def execute(self, task_id: str) -> Task:
        """Walk a Requested (or retried, Claimed) Task through the pipeline until Needs review or a stop state."""
        task = self.store.tasks[task_id]
        if task.status != "Claimed":
            check_transition("task", task.status, "Claimed")
        run = self.store.runs[task.latest_run]
        if run.status != "queued":
            raise IllegalTransition(f"{run.id} is {run.status!r}; only queued Runs execute")
        steps = self.steps
        if run.eval_run is not None:
            if self.eval_mode is None or self.eval_mode.run_url != run.eval_run:
                raise ValueError("eval mode must execute with the linked Eval Run")
            steps = self.eval_mode.steps
        context = RunContext(self._requests[task_id], task, run)
        if task.status != "Claimed":
            self._task(context, "Claimed")
        self._run(context, status="running", started=self.clock())
        try:
            self._task(context, "Collecting evidence")
            steps.collect(context)
            fix = None
            while True:
                self._task(context, "Drafting")
                steps.draft(context, fix)
                self._task(context, "Rendering")
                steps.render(context)
                self._task(context, "Checking")
                result = steps.check(context)
                summary = result["summary"]
                self._run(context, lint_hard_fails=summary["hard_fail"],
                          lint_warnings=summary["warning"], fix_attempts=context.attempt)
                if result["passed"]:
                    return self._finish(context, "succeeded", "Needs review")
                fix = [f for f in result["findings"] if f["level"] == "hard_fail" or not f["acknowledged"]]
                if context.attempt >= self.max_fix_attempts:
                    rules = ", ".join(sorted({f["rule"] for f in fix}))
                    return self._finish(context, "guardrail_blocked", "Guardrail blocked",
                                        error=f"lint failed after {context.attempt} fix attempts: {rules}")
                context.attempt += 1
        except Exception as error:
            if self.store.runs[context.run.id].is_finished:
                raise
            return self._finish(context, "failed", "Failed", error=f"{type(error).__name__}: {error}")

    def _finish(self, context: RunContext, status: str, task_status: str, error: str | None = None) -> Task:
        run = context.run
        if run.eval_run is not None:
            try:
                self.eval_mode.record_result(EvalResult(
                    factory_run_id=run.id,
                    eval_run=run.eval_run,
                    eval_case=str(context.request.eval_case),
                    status=status,
                    lint_hard_fails=run.lint_hard_fails,
                    lint_warnings=run.lint_warnings,
                    fix_attempts=run.fix_attempts,
                ))
            except Exception as write_error:
                status, task_status = "failed", "Failed"
                error = f"eval result write failed: {type(write_error).__name__}"
        self._run(context, status=status, finished=self.clock(), error=error)
        return self._task(context, task_status)

    def _task(self, context: RunContext, status: str) -> Task:
        context.task = self.store.update_task(context.task.id, status=status)
        return context.task

    def _run(self, context: RunContext, **changes: Any) -> Run:
        context.run = self.store.update_run(context.run.id, **changes)
        return context.run

    @staticmethod
    def _task_values(request: Any, key: str) -> dict[str, Any]:
        return dict(
            site=request.site,
            domain=request_domain(request.site),
            trigger=request.trigger,
            mode=request.mode,
            idempotency_key=key,
            linear_ticket=request.reply_ticket,
            musy_message=request.musy.message_id if request.musy is not None else None,
        )
