"""Prompt Versions selection and prompts-used.json (LLD §3.5)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Protocol

STEPS = ("analyze", "write-report", "write-notes", "judge")
STATUSES = ("testing", "production", "retired")
FACTORY = "AgentReady report factory"
PAGE_ID = re.compile(r"([0-9a-f]{32})(?:[?#].*)?$")
ROW_ID = re.compile(r"[0-9a-f]{32}")


class PromptSelectionError(ValueError):
    """No usable prompt for a step; the run must stop before drafting."""


@dataclass(frozen=True)
class PromptVersion:
    page_id: str
    url: str
    title: str
    status: str
    step: str
    factory: str
    version: str
    text: str

    @property
    def version_id(self) -> str:
        return f"{self.factory}/{self.step}/{self.version}"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SelectedPrompt:
    step: str
    selected_by: str
    prompt: PromptVersion


class PromptVersionsReader(Protocol):
    def rows(self) -> Iterable[PromptVersion]: ...

    def row(self, url: str) -> PromptVersion: ...


def page_id(url: str) -> str:
    match = PAGE_ID.search(url.replace("-", "").lower())
    if not match:
        raise PromptSelectionError(f"not a Prompt Versions row URL: {url}")
    return match[1]


def _text(items: list[dict]) -> str:
    return "".join(item.get("plain_text", "") for item in items)


def _property(page: dict, name: str) -> str:
    prop = page.get("properties", {}).get(name)
    if not prop:
        return ""
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in {"title", "rich_text"}:
        return _text(value or [])
    if kind in {"select", "status"}:
        return (value or {}).get("name", "")
    if kind == "number":
        return "" if value is None else f"v{value}"
    return ""


class RecordedPromptVersions:
    """Recorded Notion responses: query.json (database query) and blocks/<page id>.json."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._rows: dict[str, PromptVersion] | None = None

    def _load(self, name: Path) -> dict:
        try:
            return json.loads((self.root / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PromptSelectionError(f"could not read recorded Prompt Versions response {name}: {error}") from None

    def _row_id(self, page: dict) -> str:
        """The row's Notion page id, checked before it is used as a file name."""
        raw = page.get("id")
        if not isinstance(raw, str):
            raise PromptSelectionError(f"recorded Prompt Versions row {raw!r}: id is not a string")
        if ".." in raw or "/" in raw or "\\" in raw or Path(raw).is_absolute():
            raise PromptSelectionError(f"recorded Prompt Versions row {raw!r}: id is a path, not a Notion page id")
        id_ = raw.replace("-", "").lower()
        if not ROW_ID.fullmatch(id_):
            raise PromptSelectionError(f"recorded Prompt Versions row {raw!r}: id is not a 32-hex Notion page id")
        url = page.get("url")
        if not isinstance(url, str) or not PAGE_ID.search(url.replace("-", "").lower()):
            raise PromptSelectionError(f"recorded Prompt Versions row {raw!r}: url {url!r} has no Notion page id")
        if page_id(url) != id_:
            raise PromptSelectionError(f"recorded Prompt Versions row {raw!r}: id does not match the page id in {url}")
        return id_

    def _body(self, page_id_: str) -> str:
        blocks_dir = self.root.resolve() / "blocks"
        path = (blocks_dir / f"{page_id_}.json").resolve()
        if not path.is_relative_to(blocks_dir):
            raise PromptSelectionError(f"recorded prompt body for {page_id_} resolves outside blocks/: {path}")
        blocks = self._load(Path("blocks") / f"{page_id_}.json")
        if blocks.get("has_more"):
            raise PromptSelectionError(f"recorded prompt body for {page_id_} is incomplete (has_more)")
        lines = []
        for block in blocks.get("results", []):
            content = block.get(block.get("type"), {})
            lines.append(_text(content.get("rich_text", [])))
        return "\n".join(lines)

    def _all(self) -> dict[str, PromptVersion]:
        if self._rows is None:
            query = self._load(Path("query.json"))
            if query.get("has_more"):
                raise PromptSelectionError("recorded Prompt Versions query is incomplete (has_more)")
            pages = [(self._row_id(page), page) for page in query.get("results", [])]
            rows = {}
            for id_, page in pages:
                rows[id_] = PromptVersion(
                    page_id=id_,
                    url=page["url"],
                    title=_property(page, "Name"),
                    status=_property(page, "Status"),
                    step=_property(page, "Process step"),
                    factory=_property(page, "Factory"),
                    version=_property(page, "Version"),
                    text=self._body(id_),
                )
            self._rows = rows
        return self._rows

    def rows(self) -> Iterable[PromptVersion]:
        return list(self._all().values())

    def row(self, url: str) -> PromptVersion:
        found = self._all().get(page_id(url))
        if found is None:
            raise PromptSelectionError(f"Prompt Versions row not found: {url}")
        return found


def select_prompts(
    reader: PromptVersionsReader,
    overrides: Mapping[str, str] | None = None,
    factory: str = FACTORY,
) -> dict[str, SelectedPrompt]:
    """Pick one prompt per step: the override if given, else the single production row."""
    overrides = {str(step): str(url) for step, url in (overrides or {}).items()}
    unknown = sorted(set(overrides) - set(STEPS))
    if unknown:
        raise PromptSelectionError(f"unknown process step in prompt_overrides: {', '.join(unknown)}")
    rows = list(reader.rows())
    selected = {}
    for step in STEPS:
        if step in overrides:
            prompt = reader.row(overrides[step])
            if prompt.step != step or prompt.factory != factory:
                raise PromptSelectionError(
                    f"{step}: override {prompt.url} is for {prompt.factory!r} step {prompt.step!r}"
                )
            selected_by = "override"
        else:
            production = [row for row in rows if row.factory == factory and row.step == step and row.status == "production"]
            if len(production) != 1:
                urls = "".join(f"\n  {row.url}" for row in production)
                raise PromptSelectionError(
                    f"{step}: expected exactly one production row for {factory!r}, found {len(production)}{urls}"
                )
            prompt = production[0]
            selected_by = "production"
        if not prompt.text.strip():
            raise PromptSelectionError(f"{step}: prompt text is empty in {prompt.url}")
        if not prompt.version:
            raise PromptSelectionError(f"{step}: missing Version in {prompt.url}")
        selected[step] = SelectedPrompt(step, selected_by, prompt)
    return selected


def prompts_used(selected: Mapping[str, SelectedPrompt], run_id: str) -> dict:
    return {
        "version": 1,
        "run_id": run_id,
        "prompts": [
            {
                "step": step,
                "selected_by": item.selected_by,
                "row_url": item.prompt.url,
                "title": item.prompt.title,
                "status": item.prompt.status,
                "version_id": item.prompt.version_id,
                "sha256": item.prompt.sha256,
                "text_file": f"prompts/{step}.txt",
            }
            for step, item in selected.items()
        ],
    }


def write_prompts_used(selected: Mapping[str, SelectedPrompt], run_dir: Path, run_id: str) -> Path:
    """Write prompts/<step>.txt with the exact text used and prompts-used.json with its hashes."""
    run_dir = Path(run_dir)
    (run_dir / "prompts").mkdir(parents=True, exist_ok=True)
    for step, item in selected.items():
        (run_dir / "prompts" / f"{step}.txt").write_bytes(item.prompt.text.encode("utf-8"))
    path = run_dir / "prompts-used.json"
    path.write_text(json.dumps(prompts_used(selected, run_id), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


__all__ = [
    "FACTORY",
    "STATUSES",
    "STEPS",
    "PromptSelectionError",
    "PromptVersion",
    "PromptVersionsReader",
    "RecordedPromptVersions",
    "SelectedPrompt",
    "page_id",
    "prompts_used",
    "select_prompts",
    "write_prompts_used",
]
