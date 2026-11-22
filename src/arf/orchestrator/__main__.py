"""Module CLI: python -m arf.orchestrator dry-run REQUEST --out DIR [--force-lint-failure]."""

import argparse
from pathlib import Path

import yaml

from ..request import Request
from ..validation import validate_policy
from . import MemoryStore, Orchestrator
from .replay import ACK, GOLDEN, ReplaySteps


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m arf.orchestrator")
    commands = parser.add_subparsers(dest="command", required=True)
    dry_run = commands.add_parser("dry-run", help="walk one request through a recorded run (no DNS or network)")
    dry_run.add_argument("request", type=Path)
    dry_run.add_argument("--out", type=Path, required=True)
    dry_run.add_argument("--golden", type=Path, default=GOLDEN, help="recorded draft directory with report.md")
    dry_run.add_argument("--ack", type=Path, default=ACK, help="lint ack file for the recorded draft")
    dry_run.add_argument("--duplicate", action="store_true", help="submit the request a second time")
    dry_run.add_argument("--force-lint-failure", action="store_true", help="seed a price in every draft")
    args = parser.parse_args(argv)
    try:
        request = Request.model_validate(yaml.safe_load(args.request.read_text(encoding="utf-8")))
        validate_policy(request, "shadow", args.request.parent)
    except (OSError, ValueError, yaml.YAMLError) as error:
        parser.exit(2, f"error: {error}\n")
    store = MemoryStore()
    steps = ReplaySteps(args.out, failing_drafts=1_000 if args.force_lint_failure else 0,
                        golden=args.golden, ack=args.ack)
    orchestrator = Orchestrator(store, steps)
    submission = orchestrator.submit(request)
    task = orchestrator.execute(submission.task.id)
    if args.duplicate:
        again = orchestrator.submit(request)
        print(f"duplicate: reused={again.reused} run={again.run.id}")
    for line in store.transcript():
        print(line)
    run = store.runs[task.latest_run]
    print(f"result: task {task.id} {task.status}; run {run.id} {run.status}; fix attempts {run.fix_attempts}")
    return 0 if task.status == "Needs review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
