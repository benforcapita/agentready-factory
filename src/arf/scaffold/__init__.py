"""Fill-in skeletons for report.md and notes-for-ben.md, plus the prompts chosen for the run."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ..prompts import PromptVersionsReader, select_prompts, write_prompts_used
from ..request import Request
from .templates import LABELS, NOTES, REPORT, WEEKDAYS

Language = Literal["he", "en"]
LANGUAGES = ("he", "en")
ISRAEL = ZoneInfo("Asia/Jerusalem")
SLOT = re.compile(r"<!-- arf:slot ([a-z0-9-]+) -->")
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ScaffoldError(ValueError):
    """The run cannot be scaffolded."""


@dataclass(frozen=True)
class Scaffold:
    language: Language
    report: str
    notes: str


def slots(text: str) -> list[str]:
    return SLOT.findall(text)


def resolve_language(request: Request, language: str | None = None) -> Language:
    choice = language or request.language
    if choice == "auto":
        return "he"
    if choice not in LANGUAGES:
        raise ScaffoldError(f"unsupported language: {choice}")
    return choice


def _meeting(request: Request, language: Language) -> str:
    if request.meeting is None:
        return ""
    start = request.meeting.start.astimezone(ISRAEL)
    labels = LABELS[language]
    day = WEEKDAYS[language][start.weekday()]
    if language == "he":
        when = f"{day}, {start:%d.%m.%Y}, {start:%H:%M}"
    else:
        when = f"{day}, {start.day} {start:%B %Y}, {start:%H:%M}"
    return f"**{labels['meeting']}:** {when} ({labels['time']}), {request.meeting.link_note}\n"


def render(request: Request, run_id: str, language: str | None = None) -> Scaffold:
    if not RUN_ID.fullmatch(run_id):
        raise ScaffoldError(f"invalid run id: {run_id!r}")
    lang = resolve_language(request, language)
    business = request.business_name or urlsplit(request.site).hostname or request.site
    contact = request.contact
    meeting = _meeting(request, lang)
    report = REPORT[lang].format(
        draft="" if request.mode == "volume" else f"<!-- arf:draft run={run_id} -->\n",
        business=business,
        contact_line=f" — {contact.name}, {contact.role}" if contact else "",
        meeting_line=meeting,
    )
    notes = NOTES[lang].format(
        contact_name=contact.name if contact else LABELS[lang]["contact"],
        business=business,
        meeting_line=meeting + "\n" if meeting else "",
    )
    return Scaffold(lang, report, notes)


def scaffold(
    request: Request,
    out: Path,
    run_id: str,
    reader: PromptVersionsReader,
    language: str | None = None,
) -> Scaffold:
    """Select prompts first (a PromptSelectionError stops the run before drafting), then write the skeletons."""
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ScaffoldError(f"output directory is not empty: {out}")
    selected = select_prompts(reader, request.prompt_overrides)
    result = render(request, run_id, language)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(result.report, encoding="utf-8")
    (out / "notes-for-ben.md").write_text(result.notes, encoding="utf-8")
    write_prompts_used(selected, out, run_id)
    return result


__all__ = ["LANGUAGES", "SLOT", "Scaffold", "ScaffoldError", "render", "resolve_language", "scaffold", "slots"]
