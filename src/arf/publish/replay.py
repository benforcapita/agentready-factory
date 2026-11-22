"""Recorded publish dry runs: the T8a replay draft plus the full artifact set, uploaded to an in-memory Drive."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ..orchestrator import RunContext
from ..orchestrator.replay import ReplaySteps
from ..render import render_pdf

EVIDENCE_SKIP = {"README.md", "SHA256SUMS.txt", "build_html.py", "report.md", "report.html", "report.pdf",
                 "notes-for-ben.md"}
ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def evidence_bundle(source: Path, out: Path) -> Path:
    """Zip the recorded evidence files with fixed timestamps so the bundle hash is reproducible."""
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(Path(source).iterdir()):
            if path.is_file() and path.name not in EVIDENCE_SKIP:
                info = zipfile.ZipInfo(path.name, ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, path.read_bytes())
    return out


class PublishReplaySteps(ReplaySteps):
    """ReplaySteps that also writes report.pdf, notes-for-ben.md, evidence.zip and prompts-used.json."""

    def __init__(self, out: Path, *, pdf: bool = True, **kwargs) -> None:
        super().__init__(out, **kwargs)
        self.pdf = pdf

    def render(self, context: RunContext) -> None:
        super().render(context)
        directory = self.directory(context)
        if self.pdf:
            render_pdf((directory / "report.html").read_text(encoding="utf-8"), directory / "report.pdf")
        notes = self.golden / "notes-for-ben.md"
        if notes.is_file():
            (directory / "notes-for-ben.md").write_text(notes.read_text(encoding="utf-8"), encoding="utf-8")
        evidence_bundle(self.golden, directory / "evidence.zip")
        prompts = dict(run=context.run.id, source="recorded golden draft", prompts=[])
        (directory / "prompts-used.json").write_text(json.dumps(prompts, indent=2) + "\n", encoding="utf-8")
