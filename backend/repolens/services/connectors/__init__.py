"""Pluggable documentation sources (Jira, Slack, Notion, Google Docs, SharePoint) that feed a docs collection.

Each connector turns a remote system into ``RemoteDoc`` rows; ``sync_documents`` stores them incrementally (only
changed items are rewritten, deleted ones are removed) exactly like the Confluence importer. Credentials are
encrypted on the source and never returned by the API.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session


class ConnectorError(Exception):
    """A message that is safe to show the user (never contains a credential)."""


@dataclass
class RemoteDoc:
    external_id: str
    path: str  # relative path inside the collection, e.g. "jira/PROJ/PROJ-1.md"
    title: str
    markdown: str
    url: str = ""
    version: int = 0
    updated: datetime | None = None
    parent_path: str = ""


@dataclass
class FetchResult:
    docs: list[RemoteDoc]
    truncated: bool = False


@dataclass
class Field:
    key: str
    label: str
    type: str = "text"  # text | password | number | textarea | select
    required: bool = True
    secret: bool = False
    placeholder: str = ""
    help: str = ""
    options: list[str] = field(default_factory=list)


@dataclass
class Ctx:
    db: Session
    org_id: str
    config: dict[str, Any]
    secret: str
    max_docs: int
    transport: httpx.BaseTransport | None = None


class Connector:
    kind = ""
    label = ""
    description = ""
    needs_secret = True
    fields: list[Field] = []

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        """Normalise and check user-supplied config (raise ConnectorError with a helpful message)."""
        raise NotImplementedError

    def check(self, ctx: Ctx) -> str:
        """One cheap authenticated call. Returns a short human description of who/what we connected as."""
        raise NotImplementedError

    def fetch(self, ctx: Ctx) -> FetchResult:
        raise NotImplementedError

    def describe(self, config: dict[str, Any]) -> str:
        """Short label shown in the sources list."""
        return self.label


# Tests set e.g. transports["slack"] = httpx.MockTransport(...)
transports: dict[str, httpx.BaseTransport] = {}
REGISTRY: dict[str, Connector] = {}


def register(c: Connector) -> Connector:
    REGISTRY[c.kind] = c
    return c


def get(kind: str) -> Connector:
    if kind not in REGISTRY:
        raise ConnectorError(f"Unknown source type: {kind}")
    return REGISTRY[kind]


def load_all() -> None:
    from . import jira_source, slack  # noqa: F401  (each registers itself)
