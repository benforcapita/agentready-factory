"""Notion data model from LLD §3 (task status options from LLD §4.1)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Property:
    name: str
    type: str
    options: tuple[str, ...] = ()
    target: str | None = None


@dataclass(frozen=True)
class Schema:
    key: str
    title: str
    object: str
    parent: str | None = None
    properties: tuple[Property, ...] = ()
    fields: tuple[str, ...] = ()


MODES = ("shadow", "reviewed", "volume", "eval")

TASK_STATUSES = (
    "Requested",
    "Claimed",
    "Collecting evidence",
    "Drafting",
    "Rendering",
    "Checking",
    "Needs review",
    "Accepted",
    "Accepted with edits",
    "Rejected",
    "Failed",
    "Guardrail blocked",
    "Cancelled",
    "Rejected request",
    "Duplicate",
)

FACTORIES_HUB = Schema("factories-hub", "🏭 Factories", "page")

FACTORY_PAGE = Schema("factory-page", "AgentReady Report Factory", "page", "factories-hub")

TASKS = Schema(
    "tasks",
    "Tasks",
    "database",
    "factory-page",
    (
        Property("Name", "title"),
        Property("Status", "status", TASK_STATUSES),
        Property("Site", "url"),
        Property("Domain", "rich_text"),
        Property("Business", "rich_text"),
        Property("Trigger", "select", ("musy_lead", "named_site")),
        Property("Mode", "select", MODES),
        Property("Language", "select", ("he", "en")),
        # LLD: "bot slugs + ben".
        Property("Requested by", "select", ("ben", "chief-of-staff", "agentready-factory")),
        Property("Linear ticket", "url"),
        Property("Musy message", "rich_text"),
        Property("Musy scores", "rich_text"),
        Property("Contact", "rich_text"),
        Property("Meeting", "date"),
        Property("Idempotency key", "rich_text"),
        Property("Runs", "relation", target="runs"),
        Property("Latest run", "relation", target="runs"),
        Property("Review outcome", "select", ("Accepted", "Accepted with edits", "Rejected")),
        Property("Review notes", "rich_text"),
        Property("Reviewed by", "people"),
        Property("Command Center item", "url"),
        Property("Created", "created_time"),
        Property("Updated", "last_edited_time"),
    ),
)

RUNS = Schema(
    "runs",
    "Runs",
    "database",
    "factory-page",
    (
        Property("Run ID", "title"),
        Property("Task", "relation", target="tasks"),
        Property(
            "Status",
            "select",
            ("queued", "running", "succeeded", "guardrail_blocked", "failed", "cancelled"),
        ),
        Property("Mode", "select", MODES),
        Property("Started", "date"),
        Property("Finished", "date"),
        Property("Duration (s)", "number"),
        Property("Toolkit version", "rich_text"),
        Property("Runtime", "rich_text"),
        # Prompt Versions is PRO-404's database; its target is not checked here.
        Property("Prompt versions used", "relation"),
        Property("Prompt overrides", "checkbox"),
        Property("Evidence summary", "rich_text"),
        Property("Score", "number"),
        Property(
            "Score source",
            "select",
            ("musy", "is-agentic-existing", "is-agentic-new-scan", "none"),
        ),
        Property("Public side effects", "checkbox"),
        Property("Lint hard fails", "number"),
        Property("Lint warnings", "number"),
        Property("Lint report", "relation", target="artifacts"),
        Property("Artifacts", "relation", target="artifacts"),
        Property("Error", "rich_text"),
        Property("Retry of", "relation", target="runs"),
        Property("Eval run", "url"),
    ),
)

ARTIFACTS = Schema(
    "artifacts",
    "Artifacts",
    "database",
    "factory-page",
    (
        Property("Name", "title"),
        Property("Run", "relation", target="runs"),
        Property(
            "Kind",
            "select",
            (
                "report_md",
                "report_html",
                "report_pdf",
                "notes_for_ben",
                "evidence_bundle",
                "lint_report",
                "prompts_used",
                "manifest",
            ),
        ),
        Property("Audience", "select", ("client_draft", "private")),
        Property("Drive link", "url"),
        Property("Box path", "rich_text"),
        Property("SHA-256", "rich_text"),
        Property("Size (bytes)", "number"),
        Property("Draft marker present", "checkbox"),
    ),
)

FACTORY_CONFIG = Schema("factory-config", "Factory Config", "page", "factory-page")

# LLD §3.4 names these fields in prose without Notion types; they are the first
# column of the page's properties table.
FACTORY_CONFIG_TABLE = Schema(
    "factory-config-table",
    "Factory Config table",
    "list",
    fields=(
        "Phase",
        "Daily run cap",
        "Max concurrent runs",
        "Probe identities",
        "Probe paths",
        "Request timeout",
        "Contact line",
        "Prepared-by line",
        "Banned phrases",
        "Allowed readers",
        "Public-scan policy",
    ),
)

SCHEMAS = (
    FACTORIES_HUB,
    FACTORY_PAGE,
    TASKS,
    RUNS,
    ARTIFACTS,
    FACTORY_CONFIG,
    FACTORY_CONFIG_TABLE,
)
