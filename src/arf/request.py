"""Request schema from LLD §2.1."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    UrlConstraints,
)

type Phase = Literal["shadow", "reviewed", "volume"]
PHASES: tuple[Phase, ...] = ("shadow", "reviewed", "volume")

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
HttpsUrl = Annotated[AnyHttpUrl, UrlConstraints(allowed_schemes=["https"])]


def parse_timestamp(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("must be an ISO 8601 timestamp with a timezone") from None
    return value


def parse_date(value):
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError("must be an ISO 8601 date") from None
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(parse_timestamp)]
ScanDate = Annotated[date, BeforeValidator(parse_date)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Contact(RequestModel):
    name: NonEmptyString
    role: NonEmptyString


class Meeting(RequestModel):
    start: Timestamp
    link_note: NonEmptyString


class MusyScores(RequestModel):
    agent_ready: float = Field(allow_inf_nan=False)
    poppy: float = Field(allow_inf_nan=False)


class MusyLead(RequestModel):
    message_id: NonEmptyString
    thread_id: NonEmptyString
    received_at: Timestamp
    scores: MusyScores
    scan_date: ScanDate
    claimed_gaps: list[NonEmptyString]


class Request(RequestModel):
    version: Literal[1]
    trigger: Literal["musy_lead", "named_site"]
    site: NonEmptyString
    business_name: NonEmptyString | None = None
    contact: Contact | None = None
    meeting: Meeting | None = None
    language: Literal["auto", "he", "en"]
    mode: Literal["shadow", "reviewed", "volume", "eval"]
    musy: MusyLead | None = None
    allow_public_scan: bool = False
    ben_ok: str | None = None
    prompt_overrides: dict[
        Literal["analyze", "write-report", "write-notes", "judge"], HttpsUrl
    ] = Field(default_factory=dict)
    force: bool = False
    requested_by: NonEmptyString
    reply_ticket: NonEmptyString | None = None
    eval_case: HttpsUrl | None = None
    fixture_bundle: NonEmptyString | None = None
