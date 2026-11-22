"""Compare recorded Notion API responses with the schemas in code."""

import json
from pathlib import Path

from .schemas import SCHEMAS, Schema


def plain_text(rich_text: list | None) -> str:
    return "".join(part.get("plain_text", "") for part in rich_text or [])


def title_of(response: dict) -> str:
    if response.get("object") == "database":
        return plain_text(response.get("title"))
    for value in (response.get("properties") or {}).values():
        if value.get("type") == "title":
            return plain_text(value.get("title"))
    return ""


def relation_target(relation: dict) -> str | None:
    return relation.get("database_id") or relation.get("data_source_id")


def check_object(schema: Schema, response: dict, ids: dict[str, str] | None = None) -> list[str]:
    """Return problems in one recorded response; `ids` maps schema keys to object ids."""
    ids = ids or {}
    found = response.get("object")
    if found != schema.object:
        return [f"{schema.title}: expected a {schema.object}, found {found!r}"]
    if schema.object == "list":
        return check_table(schema, response)

    problems = []
    title = title_of(response)
    if title != schema.title:
        problems.append(f"{schema.title}: title is {title!r}")
    if schema.parent in ids:
        parent = (response.get("parent") or {}).get("page_id")
        if parent != ids[schema.parent]:
            problems.append(f"{schema.title}: parent is not the {schema.parent} page")

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
        config = actual.get(expected.type) or {}
        options = {option.get("name") for option in config.get("options", [])}
        for option in expected.options:
            if option not in options:
                problems.append(
                    f"{schema.title}: property {expected.name!r} is missing option {option!r}"
                )
        if expected.target in ids and relation_target(config) != ids[expected.target]:
            problems.append(
                f"{schema.title}: relation {expected.name!r} does not point to {expected.target}"
            )
    return problems


def check_table(schema: Schema, response: dict) -> list[str]:
    names = set()
    for block in response.get("results", []):
        if block.get("type") == "table_row":
            cells = block["table_row"].get("cells") or [[]]
            names.add(plain_text(cells[0]).strip())
    return [
        f"{schema.title}: missing field {field!r}" for field in schema.fields if field not in names
    ]


def check_recorded(directory: Path) -> dict[str, list[str]]:
    """Check `<key>.json` for every schema in a directory of recorded responses."""
    responses, results = {}, {}
    for schema in SCHEMAS:
        path = directory / f"{schema.key}.json"
        try:
            responses[schema.key] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            results[schema.key] = [f"{schema.title}: no recorded response at {path}"]
        except (OSError, ValueError) as error:
            results[schema.key] = [f"{schema.title}: cannot read {path}: {error}"]
    ids = {key: response.get("id") for key, response in responses.items() if "id" in response}
    for key, response in responses.items():
        schema = next(item for item in SCHEMAS if item.key == key)
        results[key] = check_object(schema, response, ids)
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
