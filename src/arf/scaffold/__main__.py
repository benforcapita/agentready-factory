import argparse
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..prompts import PromptSelectionError, RecordedPromptVersions
from ..request import Request
from ..validation import RequestLoader
from . import LANGUAGES, ScaffoldError, scaffold


def load_request(path: Path) -> Request:
    try:
        return Request.model_validate(yaml.load(path.read_text(encoding="utf-8"), Loader=RequestLoader))
    except (OSError, UnicodeError) as error:
        raise ScaffoldError(f"could not read request file: {error}") from None
    except ValidationError as error:
        fields = "; ".join(
            f"{'.'.join(map(str, item['loc'])) or 'request'}: {item['msg']}" for item in error.errors(include_input=False)
        )
        raise ScaffoldError(f"invalid request: {fields}") from None
    except (yaml.YAMLError, ValueError) as error:
        raise ScaffoldError(f"invalid request YAML: {error}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arf scaffold",
        description="Write report.md and notes-for-ben.md skeletons and prompts-used.json (drafts only)",
    )
    parser.add_argument("request", type=Path, help="request.yaml")
    parser.add_argument("--out", type=Path, required=True, help="empty run folder to write")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prompts", type=Path, required=True, help="recorded Prompt Versions responses")
    parser.add_argument("--lang", choices=LANGUAGES, help="override the request language")
    args = parser.parse_args(argv)
    try:
        request = load_request(args.request)
        result = scaffold(request, args.out, args.run_id, RecordedPromptVersions(args.prompts), args.lang)
    except PromptSelectionError as error:
        print(f"PromptSelectionError: {error}", file=sys.stderr)
        return 1
    except ScaffoldError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {args.out}/report.md, notes-for-ben.md, prompts-used.json ({result.language})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
