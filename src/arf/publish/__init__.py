"""Publish step (T8b, LLD §2.2): private Drive upload, Artifacts rows, Command Center review row and
the result comment text.

Publishing only finds or creates folders and uploads files; uploads inherit the private reports
folder's access and nothing here changes who can open them. Nothing is posted or messaged: the
result comment is returned as text for the factory runtime to place on the request ticket.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..notion.schemas import ARTIFACTS, RUNS, TASKS
from ..orchestrator import ISRAEL, MemoryStore, RunContext, Steps, Task, Run

REPORTS_FOLDER = "AgentReady reports"
NOTHING_SENT = "Nothing sent; nobody contacted."

ARTIFACT_FILES: tuple[tuple[str, str, str, str], ...] = (
    # file name, Artifacts Kind, Audience, MIME type
    ("report.md", "report_md", "client_draft", "text/markdown"),
    ("report.html", "report_html", "client_draft", "text/html"),
    ("report.pdf", "report_pdf", "client_draft", "application/pdf"),
    ("notes-for-ben.md", "notes_for_ben", "private", "text/markdown"),
    ("evidence.zip", "evidence_bundle", "private", "application/zip"),
    ("lint.json", "lint_report", "private", "application/json"),
    ("prompts-used.json", "prompts_used", "private", "application/json"),
    ("manifest.json", "manifest", "private", "application/json"),
)

KINDS = next(p.options for p in ARTIFACTS.properties if p.name == "Kind")
AUDIENCES = next(p.options for p in ARTIFACTS.properties if p.name == "Audience")
assert {kind for _, kind, _, _ in ARTIFACT_FILES} == set(KINDS)

# Ben's Command Center review queue. LLD §2.2 names the row; these are the fields it is written with.
COMMAND_CENTER_FIELDS = ("Name", "Type", "Status", "Task", "Run", "Drive folder", "Linear ticket")

_DRAFT_TEXT_MARKERS = ("<!-- arf:draft run=",)


class PublishError(RuntimeError):
    pass


class Drive(Protocol):
    """The only Drive operations publishing uses: folder lookup/creation and uploads."""

    def find_folder(self, name: str, parent: str | None = None) -> dict[str, str] | None: ...

    def create_folder(self, name: str, parent: str | None = None) -> dict[str, str]: ...

    def upload(self, path: Path, *, name: str, mime_type: str, parent: str) -> dict[str, str]: ...


class MemoryDrive:
    """In-process Drive stand-in; records every call."""

    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _add(self, **values: Any) -> dict[str, str]:
        file_id = f"drive-{len(self.files) + 1:04}"
        link = f"https://drive.google.com/{'drive/folders' if values['folder'] else 'file/d'}/{file_id}"
        self.files[file_id] = dict(id=file_id, webViewLink=link, **values)
        return dict(id=file_id, webViewLink=link)

    def find_folder(self, name: str, parent: str | None = None) -> dict[str, str] | None:
        self.calls.append(("find_folder", dict(name=name, parent=parent)))
        for record in self.files.values():
            if record["folder"] and record["name"] == name and record["parent"] == parent:
                return dict(id=record["id"], webViewLink=record["webViewLink"])
        return None

    def create_folder(self, name: str, parent: str | None = None) -> dict[str, str]:
        self.calls.append(("create_folder", dict(name=name, parent=parent)))
        return self._add(name=name, parent=parent, folder=True)

    def upload(self, path: Path, *, name: str, mime_type: str, parent: str) -> dict[str, str]:
        data = Path(path).read_bytes()
        self.calls.append(("upload", dict(name=name, parent=parent, mime_type=mime_type, size=len(data))))
        return self._add(name=name, parent=parent, folder=False, mime_type=mime_type,
                         sha256=hashlib.sha256(data).hexdigest())


@dataclass(frozen=True)
class Artifact:
    id: str
    name: str
    run: str
    kind: str
    audience: str
    drive_link: str
    box_path: str
    sha256: str
    size: int
    draft_marker_present: bool

    def properties(self) -> dict[str, Any]:
        return {
            "Name": self.name,
            "Run": [self.run],
            "Kind": self.kind,
            "Audience": self.audience,
            "Drive link": self.drive_link,
            "Box path": self.box_path,
            "SHA-256": self.sha256,
            "Size (bytes)": self.size,
            "Draft marker present": self.draft_marker_present,
        }


class PublishStore(MemoryStore):
    """MemoryStore plus the Artifacts database and Command Center rows; every write is logged."""

    def __init__(self) -> None:
        super().__init__()
        self.artifacts: dict[str, Artifact] = {}
        self.command_center: dict[str, dict[str, Any]] = {}
        self.task_links: dict[str, dict[str, str]] = {}
        self.run_artifacts: dict[str, tuple[str, ...]] = {}

    def create_artifact(self, **values: Any) -> Artifact:
        if values["kind"] not in KINDS or values["audience"] not in AUDIENCES:
            raise PublishError(f"invalid Artifacts row {values['kind']!r}/{values['audience']!r}")
        artifact = Artifact(id=f"artifact-{len(self.artifacts) + 1:04}", **values)
        self.artifacts[artifact.id] = artifact
        self.log.append(("create", ARTIFACTS.title, artifact.id, artifact.properties()))
        return artifact

    def create_review_row(self, **properties: Any) -> str:
        if set(properties) != set(COMMAND_CENTER_FIELDS):
            raise PublishError(f"Command Center row needs {', '.join(COMMAND_CENTER_FIELDS)}")
        row_id = f"cc-{len(self.command_center) + 1:04}"
        url = f"https://www.notion.so/{row_id}"
        self.command_center[row_id] = dict(properties, url=url)
        self.log.append(("create", "Command Center", row_id, properties))
        return url

    def set_task_link(self, task_id: str, name: str, url: str) -> None:
        if name not in {p.name for p in TASKS.properties if p.type == "url"}:
            raise PublishError(f"Tasks has no url property {name!r}")
        if task_id not in self.tasks:
            raise KeyError(task_id)
        self.task_links.setdefault(task_id, {})[name] = url
        self.log.append(("update", TASKS.title, task_id, {name: url}))

    def link_run_artifacts(self, run_id: str, artifact_ids: list[str]) -> None:
        if self.runs[run_id].is_finished:
            raise PublishError(f"{run_id} is {self.runs[run_id].status}; finished Runs are immutable")
        self.run_artifacts[run_id] = tuple(artifact_ids)
        self.log.append(("update", RUNS.title, run_id, {"Artifacts": list(artifact_ids)}))


@dataclass
class Publication:
    folder: dict[str, str]
    artifacts: list[Artifact] = field(default_factory=list)
    review_url: str | None = None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def draft_marker_present(path: Path) -> bool:
    """The draft comment marker is in text artifacts; a PDF carries it when its sibling report.html does."""
    if path.suffix == ".pdf":
        html = path.with_name("report.html")
        return html.is_file() and draft_marker_present(html)
    text = path.read_text(encoding="utf-8", errors="replace")
    return any(marker in text for marker in _DRAFT_TEXT_MARKERS)


def write_manifest(directory: Path, run: Run, task: Task) -> Path:
    files = []
    for name, kind, audience, _ in ARTIFACT_FILES:
        path = directory / name
        if name != "manifest.json" and path.is_file():
            files.append(dict(path=name, kind=kind, audience=audience, sha256=sha256_file(path),
                              size=path.stat().st_size))
    manifest = dict(run=run.id, task=task.id, domain=task.domain, mode=run.mode, files=files)
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class Publisher:
    """Uploads a passing run's files into the private reports folder and records them in Notion."""

    def __init__(self, store: PublishStore, drive: Drive, *, folder_id: str | None = None,
                 folder_name: str = REPORTS_FOLDER) -> None:
        self.store = store
        self.drive = drive
        self.folder_id = folder_id
        self.folder_name = folder_name
        self.publications: dict[str, Publication] = {}

    def reports_folder(self) -> str:
        if self.folder_id is None:
            folder = self.drive.find_folder(self.folder_name) or self.drive.create_folder(self.folder_name)
            self.folder_id = folder["id"]
        return self.folder_id

    def publish(self, context: RunContext, directory: Path) -> Publication:
        directory = Path(directory)
        task, run = context.task, context.run
        if not (directory / "report.md").is_file():
            raise PublishError(f"{directory} has no report.md")
        write_manifest(directory, run, task)
        started = (run.started or run.finished)
        date = started.astimezone(ISRAEL).date().isoformat() if started else "undated"
        folder = self.drive.create_folder(f"{date} {task.domain} {run.id}", self.reports_folder())
        publication = Publication(folder)
        for name, kind, audience, mime_type in ARTIFACT_FILES:
            path = directory / name
            if not path.is_file():
                continue
            uploaded = self.drive.upload(path, name=name, mime_type=mime_type, parent=folder["id"])
            publication.artifacts.append(self.store.create_artifact(
                name=f"{run.id} {name}", run=run.id, kind=kind, audience=audience,
                drive_link=uploaded["webViewLink"], box_path=str(path), sha256=sha256_file(path),
                size=path.stat().st_size, draft_marker_present=draft_marker_present(path),
            ))
        if not publication.artifacts:
            raise PublishError(f"{run.id} produced no artifacts")
        self.store.link_run_artifacts(run.id, [artifact.id for artifact in publication.artifacts])
        if run.mode != "eval":
            publication.review_url = self.store.create_review_row(
                Name=f"AgentReady draft review: {task.domain}",
                Type="AgentReady report review",
                Status="Needs review",
                Task=task.id,
                Run=run.id,
                **{"Drive folder": folder["webViewLink"], "Linear ticket": task.linear_ticket},
            )
            self.store.set_task_link(task.id, "Command Center item", publication.review_url)
        context.state["publication"] = publication
        self.publications[run.id] = publication
        return publication


class PublishingSteps:
    """Wraps run steps so a passing check publishes before the Run finishes.

    A publish error raises inside the run, so the orchestrator ends the Run `failed` and the Task
    `Failed` instead of leaving a Needs review Task without artifacts.
    """

    def __init__(self, steps: Steps, publisher: Publisher, directory) -> None:
        self.steps = steps
        self.publisher = publisher
        self.directory = directory

    def collect(self, context: RunContext) -> None:
        self.steps.collect(context)

    def draft(self, context: RunContext, fix: list[dict[str, Any]] | None) -> None:
        self.steps.draft(context, fix)

    def render(self, context: RunContext) -> None:
        self.steps.render(context)

    def check(self, context: RunContext) -> dict[str, Any]:
        result = self.steps.check(context)
        if result["passed"]:
            directory = Path(self.directory(context))
            (directory / "lint.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
            self.publisher.publish(context, directory)
        return result


def result_comment(task: Task, run: Run, publication: Publication | None = None) -> str:
    """Text of the result comment for the request ticket. Never posted by this module."""
    lines = [f"AgentReady factory result for {task.domain}", "",
             f"- Task: {task.id} — {task.status}",
             f"- Run: {run.id} — {run.status} (mode {run.mode})"]
    if run.lint_hard_fails is not None:
        lines.append(f"- Lint: {run.lint_hard_fails} hard fails, {run.lint_warnings} warnings, "
                     f"{run.fix_attempts} fix attempts")
    if run.error:
        lines.append(f"- Error: {run.error}")
    if publication is not None:
        lines.append(f"- Drive folder (private): {publication.folder['webViewLink']}")
        for artifact in publication.artifacts:
            marker = "draft marker" if artifact.draft_marker_present else "no draft marker"
            lines.append(f"  - {artifact.kind} ({artifact.audience}, {marker}): {artifact.drive_link}")
        if publication.review_url:
            lines.append(f"- Review: Command Center item {publication.review_url}")
        else:
            lines.append("- Review: eval run, no Command Center item")
    lines += ["", "Drafts only, for Ben's review. " + NOTHING_SENT]
    return "\n".join(lines) + "\n"
