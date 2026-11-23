"""Fill-in skeletons for report.md and notes-for-ben.md, plus the prompts chosen for the run."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ..language import detect, detect_declared
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
    choice: LanguageChoice | None = None


@dataclass(frozen=True)
class LanguageChoice:
    """The chosen report language and why; `evidence` is the file `auto` read, relative to the evidence folder."""

    language: Language
    requested: str
    reason: str
    declared: str | None = None
    evidence: str | None = None

    @property
    def note(self) -> str:
        flag = f" (declared {self.declared}; flagged for Ben)" if self.reason.startswith("unsupported:") else ""
        return f"Language: {self.language} — {self.reason}{flag}"


def slots(text: str) -> list[str]:
    return SLOT.findall(text)


def _host(site: str) -> str:
    return urlsplit(site if "//" in site else f"//{site}").hostname or ""


def _read_site_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def choose_language(request: Request, language: str | None = None, evidence_dir: Path | None = None) -> LanguageChoice:
    """Explicit `--lang` or request he|en wins; `auto` detects from the evidence (collect/site.json, else
    the readers' jina_home.html, both optional) and the site's host."""
    choice = language or request.language
    if choice != "auto":
        if choice not in LANGUAGES:
            raise ScaffoldError(f"unsupported language: {choice}")
        return LanguageChoice(choice, choice, "--lang" if language else "request.language")
    host = _host(request.site)
    if evidence_dir is not None:
        evidence_dir = Path(evidence_dir)
        site = _read_site_json(evidence_dir / "collect" / "site.json")
        if site is not None:
            canonical = _text(site.get("canonical_url"))
            found = detect_declared(
                _text(site.get("html_lang")), _text(site.get("content_language")), _host(canonical) if canonical else host
            )
            return LanguageChoice(found[0], "auto", found[1], found[2], "collect/site.json")
        for name in ("readers/jina_home.html", "jina_home.html"):
            try:
                html = (evidence_dir / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found = detect(html, None, host)
            return LanguageChoice(found[0], "auto", found[1], found[2], name)
    found = detect(None, None, host)
    return LanguageChoice(found[0], "auto", found[1], found[2])


def resolve_language(request: Request, language: str | None = None, evidence_dir: Path | None = None) -> Language:
    return choose_language(request, language, evidence_dir).language


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


def render(request: Request, run_id: str, language: str | None = None, evidence_dir: Path | None = None) -> Scaffold:
    if not RUN_ID.fullmatch(run_id):
        raise ScaffoldError(f"invalid run id: {run_id!r}")
    choice = choose_language(request, language, evidence_dir)
    lang = choice.language
    business = request.business_name or urlsplit(request.site).hostname or request.site
    contact = request.contact
    meeting = _meeting(request, lang)
    report = REPORT[lang].format(
        draft="" if request.mode == "volume" else f"<!-- arf:draft run={run_id} -->\n",
        business=business,
        contact_line=f" — {contact.name}, {contact.role}" if contact else "",
        meeting_line=meeting,
    )
    notes_header = (meeting + "\n" if meeting else "") + (f"{choice.note}\n\n" if choice.requested == "auto" else "")
    notes = NOTES[lang].format(
        contact_name=contact.name if contact else LABELS[lang]["contact"],
        business=business,
        meeting_line=notes_header,
    )
    return Scaffold(lang, report, notes, choice)


def scaffold(
    request: Request,
    out: Path,
    run_id: str,
    reader: PromptVersionsReader,
    language: str | None = None,
    evidence_dir: Path | None = None,
) -> Scaffold:
    """Select prompts first (a PromptSelectionError stops the run before drafting), then write the skeletons.

    An `auto` run also writes language.json (chosen language, reason, declared tag, evidence file)."""
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ScaffoldError(f"output directory is not empty: {out}")
    selected = select_prompts(reader, request.prompt_overrides)
    result = render(request, run_id, language, evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(result.report, encoding="utf-8")
    (out / "notes-for-ben.md").write_text(result.notes, encoding="utf-8")
    if result.choice is not None and result.choice.requested == "auto":
        metadata = {"run_id": run_id, **asdict(result.choice)}
        (out / "language.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_prompts_used(selected, out, run_id)
    return result


__all__ = [
    "LANGUAGES",
    "SLOT",
    "LanguageChoice",
    "Scaffold",
    "ScaffoldError",
    "choose_language",
    "render",
    "resolve_language",
    "scaffold",
    "slots",
]
