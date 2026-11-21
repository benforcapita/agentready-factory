"""The arf command-line interface."""

import argparse
from pathlib import Path
import sys

from .request import PHASES
from .validation import InvalidRequest, validate_request


STUBS = {
    "collect": "Collect evidence",
    "scaffold": "Scaffold a report",
    "render": "Render a report",
    "lint": "Check a draft against the guardrails",
    "notion": "Run Notion tooling",
    "replay": "Replay a frozen evidence bundle",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arf", description="AgentReady report factory")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="Validate a request YAML file")
    validate.add_argument("request", type=Path, help="Path to the request YAML file")
    validate.add_argument(
        "--phase",
        choices=PHASES,
        default="shadow",
        help="Current Factory Config phase (default: shadow)",
    )

    for command, help_text in STUBS.items():
        commands.add_parser(command, help=help_text, description=help_text)

    args = parser.parse_args(argv)
    if args.command != "validate":
        print(f"arf {args.command}: not implemented", file=sys.stderr)
        return 1

    try:
        validate_request(args.request, phase=args.phase)
    except InvalidRequest as error:
        print(error, file=sys.stderr)
        return 1

    print("Request is valid")
    return 0
