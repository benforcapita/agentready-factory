"""The arf command-line interface."""

import argparse
from importlib import import_module
from pathlib import Path
import sys

from .collect import collect
from .collect.transport import Refused
from .notion import check_recorded, report
from .request import PHASES
from .validation import InvalidRequest, validate_request


DELEGATED = {
    "scaffold": "arf.scaffold.__main__",
    "render": "arf.render.__main__",
    "lint": "arf.lint.__main__",
}

STUBS = {
    "collect": "Collect evidence",
    "scaffold": "Scaffold a report",
    "render": "Render a report",
    "lint": "Check a draft against the guardrails",
    "notion": "Run Notion tooling",
    "replay": "Replay a frozen evidence bundle",
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in DELEGATED:
        return import_module(DELEGATED[argv[0]]).main(argv[1:])

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
        subcommand = commands.add_parser(command, help=help_text, description=help_text)
        if command == "collect":
            subcommand.add_argument("request", type=Path)
            subcommand.add_argument("--out", type=Path, required=True)
            subcommand.add_argument("--phase", choices=PHASES, default="shadow")
        if command == "notion":
            notion = subcommand.add_subparsers(dest="notion_command", required=True)
            check_schema = notion.add_parser(
                "check-schema", help="Check recorded Notion API responses against the schemas"
            )
            check_schema.add_argument(
                "recorded", type=Path, help="Directory of recorded <key>.json responses"
            )

    args = parser.parse_args(argv)
    if args.command == "collect":
        try:
            request, warnings = validate_request(args.request, phase=args.phase)
            for warning in warnings:
                print(f"warning: {warning}", file=sys.stderr)
            collect(request.site, args.out)
        except (InvalidRequest, Refused, OSError, ValueError) as error:
            print(f"arf collect: {error}", file=sys.stderr)
            return 1
        print(f"Evidence collected in {args.out}")
        return 0
    if args.command == "notion":
        output, passed = report(check_recorded(args.recorded))
        print(output, file=sys.stdout if passed else sys.stderr)
        return 0 if passed else 1
    if args.command != "validate":
        print(f"arf {args.command}: not implemented", file=sys.stderr)
        return 1

    try:
        _, warnings = validate_request(args.request, phase=args.phase)
    except InvalidRequest as error:
        print(error, file=sys.stderr)
        return 1

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print("Request is valid")
    return 0
