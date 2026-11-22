"""Notion schemas for the factory (LLD §3).

PROVISIONAL: LLD §3 was not readable when this was written (PRO-510). Property
names, types and options below must be replaced with §3's exact contract.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Property:
    name: str
    type: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Schema:
    key: str
    title: str
    object: str
    properties: tuple[Property, ...] = field(default_factory=tuple)


PHASES = ("shadow", "reviewed", "volume")
RUN_STATES = (
    "queued",
    "collecting",
    "drafting",
    "linting",
    "publishing",
    "needs_review",
    "done",
    "failed",
    "guardrail_blocked",
)

FACTORIES_HUB = Schema("factories-hub", "Factories", "page", (Property("title", "title"),))

TASKS = Schema(
    "tasks",
    "Tasks",
    "database",
    (
        Property("Name", "title"),
        Property("Status", "select", ("Queued", "Running", "Needs review", "Done", "Failed")),
        Property("Site", "url"),
        Property("Trigger", "select", ("musy_lead", "named_site")),
        Property("Mode", "select", ("shadow", "reviewed", "volume", "eval")),
        Property("Language", "select", ("auto", "he", "en")),
        Property("Requested by", "rich_text"),
        Property("Request ticket", "url"),
        Property("Runs", "relation"),
    ),
)

RUNS = Schema(
    "runs",
    "Runs",
    "database",
    (
        Property("Run ID", "title"),
        Property("Task", "relation"),
        Property("State", "select", RUN_STATES),
        Property("Phase", "select", PHASES),
        Property("Attempts", "number"),
        Property("Started", "date"),
        Property("Finished", "date"),
        Property("Prompts used", "rich_text"),
        Property("Artifacts", "relation"),
    ),
)

ARTIFACTS = Schema(
    "artifacts",
    "Artifacts",
    "database",
    (
        Property("Name", "title"),
        Property("Run", "relation"),
        Property(
            "Kind",
            "select",
            ("report.md", "report.html", "report.pdf", "notes-for-ben.md", "evidence"),
        ),
        Property("Drive file", "url"),
        Property("SHA-256", "rich_text"),
    ),
)

FACTORY_CONFIG = Schema(
    "factory-config",
    "Factory Config",
    "page",
    (
        Property("title", "title"),
        Property("Phase", "select", PHASES),
        Property("Public scans", "checkbox"),
    ),
)

SCHEMAS = (FACTORIES_HUB, TASKS, RUNS, ARTIFACTS, FACTORY_CONFIG)
