"""Module CLI: python -m arf.orchestrator dry-run REQUEST --out DIR [--force-lint-failure]."""

import argparse
from contextlib import nullcontext
from dataclasses import asdict
import json
from pathlib import Path

import yaml

from ..replay import network_disabled
from ..request import Request
from ..validation import RequestLoader, validate_policy
from . import MemoryStore, Orchestrator
from .eval import EvalMode, MockEvalDB
from .replay import ACK, GOLDEN, ReplaySteps


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m arf.orchestrator")
    commands = parser.add_subparsers(dest="command", required=True)
    dry_run = commands.add_parser("dry-run", help="walk one request through a recorded run (no DNS or network)")
    dry_run.add_argument("request", type=Path)
    dry_run.add_argument("--out", type=Path, required=True)
    dry_run.add_argument("--golden", type=Path, default=GOLDEN, help="recorded draft directory for non-eval runs")
    dry_run.add_argument("--ack", type=Path, default=ACK, help="lint ack file for the recorded draft")
    dry_run.add_argument("--duplicate", action="store_true", help="submit the request a second time")
    dry_run.add_argument("--force-lint-failure", action="store_true", help="seed a price in every draft")
    args = parser.parse_args(argv)
    try:
        request = Request.model_validate(yaml.load(args.request.read_text(encoding="utf-8"), Loader=RequestLoader))
        validate_policy(request, "shadow", args.request.parent)
    except (OSError, ValueError, yaml.YAMLError) as error:
        parser.exit(2, f"error: {error}\n")
    store = MemoryStore()
    is_eval = request.mode == "eval"
    golden = args.request.parent / request.fixture_bundle if is_eval else args.golden
    steps = ReplaySteps(args.out, failing_drafts=1_000 if args.force_lint_failure else 0,
                        golden=golden, ack=args.ack)
    mock_evals = eval_mode = None
    if is_eval:
        mock_evals = MockEvalDB("https://example.com/eval-runs/local", frozenset({str(request.eval_case)}))
        eval_mode = EvalMode(mock_evals.run_url, steps, mock_evals.record_result)
    orchestrator = Orchestrator(store, steps, eval_mode=eval_mode)
    submission = orchestrator.submit(request)
    with network_disabled() if eval_mode else nullcontext():
        task = orchestrator.execute(submission.task.id)
    if args.duplicate:
        again = orchestrator.submit(request)
        print(f"duplicate: reused={again.reused} run={again.run.id}"
              + ("; resubmit with force: true to retry" if again.needs_force else ""))
    for line in store.transcript():
        print(line)
    if mock_evals:
        for result in mock_evals.results:
            print(f"mock Eval Result: {json.dumps(asdict(result), sort_keys=True)}")
    run = store.runs[task.latest_run]
    print(f"result: task {task.id} {task.status}; run {run.id} {run.status}; fix attempts {run.fix_attempts}")
    return 0 if task.status == "Needs review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
