import argparse
import json
import sys
from pathlib import Path

from ..validation import InvalidRequest, validate_request
from . import collect_scores
from ..readers.adapters import urllib_fetch


def recorded_fetch(path):
    data = Path(path).read_bytes()
    status = json.loads(data).get("status", 200)
    status = status if isinstance(status, int) else 200

    def fetch(method, url, headers, body=None):
        return status, data

    return fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m arf.score",
        description="Collect Musy and is-agentic scores; a new public scan needs allow_public_scan and ben_ok",
    )
    parser.add_argument("request")
    parser.add_argument("--out", required=True)
    parser.add_argument("--phase", choices=["shadow", "reviewed", "volume"], default="shadow")
    parser.add_argument("--api-fixture", help="replay a recorded is-agentic API response instead of calling it")
    args = parser.parse_args(argv)
    try:
        request, _ = validate_request(Path(args.request), args.phase)
    except InvalidRequest as error:
        print(f"invalid request:\n{error}", file=sys.stderr)
        return 1
    fetch = recorded_fetch(args.api_fixture) if args.api_fixture else urllib_fetch
    summary = collect_scores(request, args.out, fetch=fetch)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
