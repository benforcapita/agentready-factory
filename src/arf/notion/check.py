"""Compare recorded Notion API objects with the schemas in code."""

import json
from pathlib import Path

from .schemas import SCHEMAS, Schema


def check_object(schema: Schema, response: dict) -> list[str]:
    """Return problems found in one retrieved Notion page or database."""
    found = response.get("object")
    if found != schema.object:
        return [f"{schema.title}: expected a {schema.object}, found {found!r}"]

    problems = []
    properties = response.get("properties") or {}
    for expected in schema.properties:
        actual = properties.get(expected.name)
        if actual is None:
            problems.append(
                f"{schema.title}: missing property {expected.name!r} ({expected.type})"
            )
            continue
        if actual.get("type") != expected.type:
            problems.append(
                f"{schema.title}: property {expected.name!r} has type "
                f"{actual.get('type')!r}, expected {expected.type!r}"
            )
            continue
        if expected.options and schema.object == "database":
            options = {
                option.get("name")
                for option in (actual.get(expected.type) or {}).get("options", [])
            }
            for option in expected.options:
                if option not in options:
                    problems.append(
                        f"{schema.title}: property {expected.name!r} "
                        f"is missing option {option!r}"
                    )
    return problems


def check_recorded(directory: Path) -> dict[str, list[str]]:
    """Check `<key>.json` for every schema in a directory of recorded responses."""
    results = {}
    for schema in SCHEMAS:
        path = directory / f"{schema.key}.json"
        try:
            response = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            results[schema.key] = [f"{schema.title}: no recorded response at {path}"]
            continue
        except (OSError, ValueError) as error:
            results[schema.key] = [f"{schema.title}: cannot read {path}: {error}"]
            continue
        results[schema.key] = check_object(schema, response)
    return results


def report(results: dict[str, list[str]]) -> tuple[str, bool]:
    lines = []
    for schema in SCHEMAS:
        problems = results.get(schema.key, [])
        lines.append(f"{'ok' if not problems else 'FAIL':<6}{schema.title}")
        lines.extend(f"      {problem}" for problem in problems)
    passed = not any(results.values())
    lines.append("Schema check passed" if passed else "Schema check failed")
    return "\n".join(lines), passed
