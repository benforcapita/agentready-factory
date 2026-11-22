"""Offline replay of a frozen evidence bundle and its golden structure checks (LLD §11)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import socket
from contextlib import contextmanager
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from ..lint import lint, serialize
from ..prompts import RecordedPromptVersions, select_prompts, write_prompts_used
from ..readers import replay as replay_readers
from ..render import draft_comment, render_html, render_pdf

PDF_PAGES = (2, 4)
DRAFTS = ("report.md", "notes-for-ben.md")


class ReplayError(ValueError):
    """The bundle cannot be replayed."""


class NetworkDisabled(OSError):
    """A replay step tried to open a network connection."""


@contextmanager
def network_disabled():
    """Refuse every socket connection and DNS lookup inside the block."""

    def refuse(*args, **kwargs):
        raise NetworkDisabled("network access is disabled during arf replay")

    saved = {
        (socket.socket, "connect"): socket.socket.connect,
        (socket.socket, "connect_ex"): socket.socket.connect_ex,
        (socket, "create_connection"): socket.create_connection,
        (socket, "getaddrinfo"): socket.getaddrinfo,
        (socket, "gethostbyname"): socket.gethostbyname,
        (socket, "gethostbyname_ex"): socket.gethostbyname_ex,
    }
    for (owner, name) in saved:
        setattr(owner, name, refuse)
    try:
        yield
    finally:
        for (owner, name), value in saved.items():
            setattr(owner, name, value)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_bundle(bundle: Path) -> list[dict]:
    """Check every file listed in SHA256SUMS.txt against its hash; the bundle is frozen."""
    bundle = Path(bundle)
    sums = bundle / "SHA256SUMS.txt"
    if not sums.is_file():
        raise ReplayError(f"frozen bundle needs SHA256SUMS.txt: {bundle}")
    entries = []
    for number, line in enumerate(sums.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line.strip())
        if not match:
            raise ReplayError(f"SHA256SUMS.txt line {number} is not '<sha256>  <file>'")
        name = match[2]
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ReplayError(f"SHA256SUMS.txt line {number}: {name} is outside the bundle")
        path = bundle / name
        if not path.is_file():
            raise ReplayError(f"bundle file missing: {name}")
        if sha256(path) != match[1]:
            raise ReplayError(f"bundle file changed: {name} does not match SHA256SUMS.txt")
        entries.append({"file": name, "sha256": match[1]})
    missing = [name for name in (*DRAFTS, "report.html") if name not in {entry["file"] for entry in entries}]
    if missing:
        raise ReplayError(f"SHA256SUMS.txt does not cover {', '.join(missing)}")
    return entries


class _Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections, self.tables = [], []
        self._heading = None
        self._row = None

    def handle_starttag(self, tag, attrs):
        if tag in {"h1", "h2"}:
            self._heading = []
        elif tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = 0
        elif tag in {"td", "th"} and self._row is not None:
            self._row += 1

    def handle_endtag(self, tag):
        if tag in {"h1", "h2"} and self._heading is not None:
            self.sections.append(f"{tag}: " + " ".join(unescape("".join(self._heading)).split()))
            self._heading = None
        elif tag == "tr" and self._row is not None:
            self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._heading is not None:
            self._heading.append(data)


def structure(document: str) -> dict:
    """Section order (h1/h2 text) and table shapes (cells per row, header row first)."""
    parser = _Structure()
    parser.feed(document)
    return {"sections": parser.sections, "tables": parser.tables}


def _title(document: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", document, re.S)
    return unescape(match[1]) if match else None


def replay(
    bundle: Path,
    out: Path,
    prompts: Path,
    *,
    run_id: str = "replay",
    final: bool = False,
    ack: Path | None = None,
    lang: str = "he",
) -> dict:
    """Rebuild the run outputs from a frozen bundle and recorded Prompt Versions with network disabled.

    Writes readers/, prompts/, prompts-used.json, report.md, notes-for-ben.md, report.html,
    report.pdf, lint.json and replay.json (the golden checks) to the empty folder `out`.
    Shadow replays carry the draft marker; `final` reproduces the frozen report.html.
    """
    bundle, out = Path(bundle), Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ReplayError(f"output directory is not empty: {out}")
    draft_comment(run_id)
    with network_disabled():
        inputs = verify_bundle(bundle)
        selected = select_prompts(RecordedPromptVersions(prompts))
        out.mkdir(parents=True, exist_ok=True)
        write_prompts_used(selected, out, run_id)
        readers = replay_readers(bundle, out / "readers")
        for name in DRAFTS:
            shutil.copyfile(bundle / name, out / name)
        if not final:
            source = (out / "report.md").read_text(encoding="utf-8")
            (out / "report.md").write_text(f"{draft_comment(run_id)}\n{source}", encoding="utf-8")
        golden_html = (bundle / "report.html").read_text(encoding="utf-8")
        document = render_html(
            (out / "report.md").read_text(encoding="utf-8"),
            lang=lang,
            title=_title(golden_html),
            draft_marker=not final,
        )
        (out / "report.html").write_text(document, encoding="utf-8")
        pages = render_pdf(document, out / "report.pdf")
        lint_result = lint(out, "shadow", ack, final=final)
        (out / "lint.json").write_text(serialize(lint_result), encoding="utf-8")

    expected, actual = structure(golden_html), structure(document)
    checks = {
        "sections_match": actual["sections"] == expected["sections"],
        "tables_match": actual["tables"] == expected["tables"],
        "lint_passed": lint_result["passed"],
        "lint_hard_fails": lint_result["summary"]["hard_fail"],
        "pdf_pages": pages,
        "pdf_pages_ok": PDF_PAGES[0] <= pages <= PDF_PAGES[1],
    }
    if final:
        checks["html_identical"] = document == golden_html
    passed = all(value for key, value in checks.items() if key not in {"lint_hard_fails", "pdf_pages"})
    result = {
        "version": 1,
        "run_id": run_id,
        "final": final,
        "bundle": {"files": inputs},
        "prompts": {step: item.prompt.version_id for step, item in selected.items()},
        "readers": readers,
        "golden": {"expected": expected, "actual": actual},
        "checks": checks,
        "outputs": {
            name: sha256(out / name)
            for name in ("report.md", "notes-for-ben.md", "report.html", "report.pdf", "lint.json", "prompts-used.json")
        },
        "passed": passed,
    }
    (out / "replay.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


__all__ = ["PDF_PAGES", "NetworkDisabled", "ReplayError", "network_disabled", "replay", "structure", "verify_bundle"]
