"""Module CLI, pending shared arf CLI wiring."""

import argparse
from pathlib import Path

from . import lint, serialize


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m arf.lint")
    parser.add_argument("draft_dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--ack", type=Path)
    parser.add_argument("--phase", choices=("shadow", "reviewed", "volume"), default="shadow")
    args = parser.parse_args(argv)
    output = args.out if args.out is not None else args.draft_dir / "lint.json"
    try:
        protected = [args.draft_dir / name for name in
                     ("report.md", "report.html", "notes-for-ben.md", "lint-ack.json")]
        if args.ack:
            protected.append(args.ack)
        if output.resolve() in {path.resolve() for path in protected}:
            raise ValueError("output must not overwrite an input artifact or ack file")
        result = lint(args.draft_dir, args.phase, args.ack)
        output.write_text(serialize(result), encoding="utf-8")
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    counts = result["summary"]
    print(f"{'PASS' if result['passed'] else 'FAIL'}: "
          f"{counts['hard_fail']} hard fails, {counts['unacknowledged_warning']} unacknowledged warnings, "
          f"{counts['acknowledged']} acknowledged warnings")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
