import argparse
import json
import os
import sys

from . import ReaderError, read_site, replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m arf.readers",
        description="Read evidence pages (Jina, TinyFish, schema.org validator) and extract JSON-LD",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay", help="rebuild outputs from a recorded fixture bundle")
    replay_parser.add_argument("bundle")
    replay_parser.add_argument("--out", required=True)
    read_parser = commands.add_parser("read", help="live read (uses JINA_API_KEY / TINYFISH_API_KEY)")
    read_parser.add_argument("site")
    read_parser.add_argument("--path", action="append", dest="paths")
    read_parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "replay":
            summary = replay(args.bundle, args.out)
        else:
            summary = read_site(
                args.site, args.paths or ["/"], args.out,
                jina_key=os.environ.get("JINA_API_KEY"),
                tinyfish_key=os.environ.get("TINYFISH_API_KEY"),
            )
    except (ReaderError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
