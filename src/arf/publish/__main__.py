"""Module CLI: python -m arf.publish dry-run REQUEST --out DIR [--eval] [--no-pdf] [--force-lint-failure]."""

import argparse
from pathlib import Path

import yaml

from ..orchestrator import Orchestrator
from ..orchestrator.replay import ACK, GOLDEN
from ..request import Request
from ..validation import validate_policy
from . import MemoryDrive, Publisher, PublishingSteps, PublishStore, result_comment
from .replay import PublishReplaySteps


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m arf.publish")
    commands = parser.add_subparsers(dest="command", required=True)
    dry_run = commands.add_parser("dry-run", help="run and publish one recorded request to an in-memory Drive and Notion")
    dry_run.add_argument("request", type=Path)
    dry_run.add_argument("--out", type=Path, required=True)
    dry_run.add_argument("--golden", type=Path, default=GOLDEN, help="recorded draft directory with report.md")
    dry_run.add_argument("--ack", type=Path, default=ACK, help="lint ack file for the recorded draft")
    dry_run.add_argument("--eval", action="store_true", help="run in eval mode (no Command Center item)")
    dry_run.add_argument("--no-pdf", dest="pdf", action="store_false", help="skip report.pdf")
    dry_run.add_argument("--force-lint-failure", action="store_true", help="seed a price in every draft")
    args = parser.parse_args(argv)
    try:
        request = Request.model_validate(yaml.safe_load(args.request.read_text(encoding="utf-8")))
        validate_policy(request, "shadow", args.request.parent)
    except (OSError, ValueError, yaml.YAMLError) as error:
        parser.exit(2, f"error: {error}\n")
    if args.eval:
        request = request.model_copy(update={"mode": "eval"})
    store = PublishStore()
    drive = MemoryDrive()
    replay = PublishReplaySteps(args.out, pdf=args.pdf, failing_drafts=1_000 if args.force_lint_failure else 0,
                                golden=args.golden, ack=args.ack)
    publisher = Publisher(store, drive)
    orchestrator = Orchestrator(store, PublishingSteps(replay, publisher, replay.directory))
    task = orchestrator.execute(orchestrator.submit(request).task.id)
    run = store.runs[task.latest_run]
    publication = publisher.publications.get(run.id)
    for line in store.transcript():
        print(line)
    for name, values in drive.calls:
        print(f"drive {name} {values}")
    comment = result_comment(task, run, publication)
    run_dir = args.out / run.id
    if run_dir.is_dir():
        (run_dir / "result-comment.md").write_text(comment, encoding="utf-8")
    print(comment, end="")
    print(f"result: task {task.id} {task.status}; run {run.id} {run.status}; "
          f"artifacts {len(store.artifacts)}; command center {len(store.command_center)}")
    return 0 if task.status == "Needs review" and store.artifacts else 1


if __name__ == "__main__":
    raise SystemExit(main())
