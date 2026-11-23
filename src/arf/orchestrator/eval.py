"""Adapter contract for reporting a factory run to the external eval harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from . import Steps


@dataclass(frozen=True)
class EvalResult:
    factory_run_id: str
    eval_run: str
    eval_case: str
    status: str
    lint_hard_fails: int | None
    lint_warnings: int | None
    fix_attempts: int


@dataclass(frozen=True)
class EvalMode:
    """A harness-provided Eval Run URL, fixture-only steps and result writer."""

    run_url: str
    steps: Steps
    record_result: Callable[[EvalResult], None]


@dataclass
class MockEvalDB:
    """In-memory Eval Cases, Eval Runs and Eval Results for the dry run."""

    run_url: str
    cases: frozenset[str]
    results: list[EvalResult] = field(default_factory=list, init=False)

    def record_result(self, result: EvalResult) -> None:
        if result.eval_run != self.run_url or result.eval_case not in self.cases:
            raise ValueError("unknown mock eval run or case")
        self.results.append(result)
