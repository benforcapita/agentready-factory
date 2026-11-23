"""Recorded steps for orchestrator dry runs: the Lehamim golden draft and the real linter, no network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..lint import lint
from ..render import draft_comment, render_html
from . import RunContext

GOLDEN = Path("fixtures/golden/lehamim")
ACK = Path("fixtures/lint/lehamim/lint-ack.json")
SEEDED_PRICE = "\n\n50 USD"


class ReplaySteps:
    """Writes each run's draft under OUT/<run id>/. Fixture paths default to the repository checkout.

    `failing_drafts` drafts in a row carry a seeded price in §5 (a lint hard fail), so 1 shows a
    successful fix and 3 or more exhausts the two fix attempts.
    """

    def __init__(self, out: Path, *, failing_drafts: int = 0, golden: Path = GOLDEN, ack: Path = ACK) -> None:
        self.out = Path(out)
        self.failing_drafts = failing_drafts
        self.golden = Path(golden)
        self.ack = Path(ack)
        self.drafts = 0

    def directory(self, context: RunContext) -> Path:
        return self.out / context.run.id

    def collect(self, context: RunContext) -> None:
        self.directory(context).mkdir(parents=True, exist_ok=False)

    def draft(self, context: RunContext, fix: list[dict[str, Any]] | None) -> None:
        report = draft_comment(context.run.id) + "\n\n" + (self.golden / "report.md").read_text(encoding="utf-8")
        if self.drafts < self.failing_drafts:
            report = report.replace("## 5. הצעד הבא", "## 5. הצעד הבא" + SEEDED_PRICE, 1)
        self.drafts += 1
        (self.directory(context) / "report.md").write_text(report, encoding="utf-8")

    def render(self, context: RunContext) -> None:
        directory = self.directory(context)
        html = render_html((directory / "report.md").read_text(encoding="utf-8"), run_id=context.run.id)
        (directory / "report.html").write_text(html, encoding="utf-8")

    def check(self, context: RunContext) -> dict[str, Any]:
        return lint(self.directory(context), phase="shadow", ack_path=self.ack)
