"""Connector interfaces.

A connector is the only place where Primee touches data it does not own.  In
Step One **no connector talks to a real account or to the network**.  Each kind
ships two implementations:

* ``NotConfigured*`` - the default.  Reports ``not_configured`` and returns no
  data at all.  Skills must surface this honestly instead of inventing numbers.
* ``Mock*`` - reads a synthetic JSON fixture from disk for tests and local
  demonstrations.  Fixtures contain invented, non-personal sample values.

Connectors are read-only by construction: the interfaces below expose no write
method.  A future write-capable connector would need a new interface, a new
permission and an approval gate.

Connectors never raise into a skill.  Every failure becomes a
:class:`ConnectorResponse` with status ``error`` and a sanitized message.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..core.redaction import redact_text

CONFIGURED = "configured"
NOT_CONFIGURED = "not_configured"
DENIED = "denied"
ERROR = "error"

MAX_FIXTURE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ConnectorResponse:
    """The single response shape every connector returns."""

    status: str
    source_id: str
    observed_at: str = ""
    data: Any = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == CONFIGURED

    def describe(self) -> dict:
        return {
            "status": self.status,
            "source_id": self.source_id,
            "observed_at": self.observed_at,
            "message": self.message,
        }


class Connector(ABC):
    """Base class for every connector."""

    kind: str = "unknown"

    def __init__(self, source_id: str, *, provider: str = "none") -> None:
        self.source_id = source_id
        self.provider = provider

    @property
    def configured(self) -> bool:
        return self.provider not in ("", "none")

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "provider": self.provider,
            "source_id": self.source_id,
            "configured": self.configured,
            "read_only": True,
        }

    def not_configured(self, detail: str) -> ConnectorResponse:
        return ConnectorResponse(
            status=NOT_CONFIGURED, source_id=self.source_id, message=detail
        )

    def failure(self, detail: str) -> ConnectorResponse:
        return ConnectorResponse(
            status=ERROR, source_id=self.source_id, message=redact_text(detail)
        )


class DeniedConnector(Connector):
    """Stand-in returned when a skill may not read a connector.

    Primee Core substitutes this for the real connector when the skill did not
    declare ``connector.<kind>.read``, or when the permission policy refuses it.
    It answers every fetch with ``denied`` and no data, so a skill can never
    silently receive data it was not permitted to see.
    """

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(source_id=f"{kind}:denied", provider="none")
        self.kind = kind
        self.reason = reason

    def _denied(self) -> ConnectorResponse:
        return ConnectorResponse(
            status=DENIED, source_id=self.source_id, message=redact_text(self.reason)
        )

    def fetch_series(self, key: str, period: str) -> ConnectorResponse:
        return self._denied()

    def fetch_recent(self, limit: int) -> ConnectorResponse:
        return self._denied()

    def fetch_events(self, day_iso: str) -> ConnectorResponse:
        return self._denied()

    def fetch_snapshot(self, source_ids: tuple[str, ...]) -> ConnectorResponse:
        return self._denied()


class FixtureConnector(Connector):
    """Shared loading logic for the synthetic-data mock connectors."""

    def __init__(self, source_id: str, fixture: Optional[str], *, provider: str = "mock") -> None:
        super().__init__(source_id, provider=provider)
        self.fixture = fixture

    def load_fixture(self) -> tuple[Optional[dict], Optional[ConnectorResponse]]:
        if not self.fixture:
            return None, self.not_configured(
                "This mock connector has no 'fixture' file configured."
            )
        path = Path(self.fixture)
        if not path.is_file():
            return None, self.failure("The configured fixture file was not found.")
        try:
            if path.stat().st_size > MAX_FIXTURE_BYTES:
                return None, self.failure("The configured fixture file is too large.")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None, self.failure("The configured fixture file could not be read.")
        except json.JSONDecodeError:
            return None, self.failure("The configured fixture file is not valid JSON.")
        if not isinstance(payload, dict):
            return None, self.failure("The configured fixture must contain a JSON object.")
        return payload, None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
class MetricsSource(Connector):
    kind = "metrics"

    @abstractmethod
    def fetch_series(self, key: str, period: str) -> ConnectorResponse:
        """Return ``{"points": [{"date": "...", "value": <number>}, ...]}``."""


class NotConfiguredMetricsSource(MetricsSource):
    def __init__(self) -> None:
        super().__init__(source_id="metrics:none", provider="none")

    def fetch_series(self, key: str, period: str) -> ConnectorResponse:
        return self.not_configured(
            "No metrics provider is configured, so no numbers are available."
        )


class MockMetricsSource(FixtureConnector, MetricsSource):
    kind = "metrics"

    def __init__(self, fixture: Optional[str]) -> None:
        FixtureConnector.__init__(self, "metrics:mock", fixture)

    def fetch_series(self, key: str, period: str) -> ConnectorResponse:
        payload, failure = self.load_fixture()
        if failure is not None:
            return failure
        series = payload.get("series", {})
        if not isinstance(series, dict) or key not in series:
            return self.not_configured(
                f"The metrics fixture does not contain a series named '{key}'."
            )
        points = series[key]
        if not isinstance(points, list):
            return self.failure(f"Series '{key}' in the fixture is not a list of points.")
        cleaned = []
        for point in points:
            if not isinstance(point, dict):
                return self.failure(f"Series '{key}' contains a malformed point.")
            date = str(point.get("date", "")).strip()
            value = point.get("value")
            if not date or not isinstance(value, (int, float)) or isinstance(value, bool):
                return self.failure(f"Series '{key}' contains a malformed point.")
            cleaned.append({"date": date, "value": float(value)})
        cleaned.sort(key=lambda item: item["date"])
        return ConnectorResponse(
            status=CONFIGURED,
            source_id=self.source_id,
            observed_at=str(payload.get("observed_at", "")),
            data={"key": key, "period": period, "points": cleaned},
        )


# ---------------------------------------------------------------------------
# Email (read-only)
# ---------------------------------------------------------------------------
class EmailSource(Connector):
    kind = "email"

    @abstractmethod
    def fetch_recent(self, limit: int) -> ConnectorResponse:
        """Return ``{"messages": [...]}`` with headers and flags, never bodies."""


class NotConfiguredEmailSource(EmailSource):
    def __init__(self) -> None:
        super().__init__(source_id="email:none", provider="none")

    def fetch_recent(self, limit: int) -> ConnectorResponse:
        return self.not_configured("No email provider is configured.")


class MockEmailSource(FixtureConnector, EmailSource):
    kind = "email"

    def __init__(self, fixture: Optional[str]) -> None:
        FixtureConnector.__init__(self, "email:mock", fixture)

    def fetch_recent(self, limit: int) -> ConnectorResponse:
        payload, failure = self.load_fixture()
        if failure is not None:
            return failure
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            return self.failure("The email fixture must contain a 'messages' list.")
        cleaned = []
        for message in messages[: max(0, int(limit))]:
            if not isinstance(message, dict):
                return self.failure("The email fixture contains a malformed message.")
            cleaned.append(
                {
                    "id": str(message.get("id", "")),
                    "subject": str(message.get("subject", "")),
                    "from_label": str(message.get("from_label", "")),
                    "received_at": str(message.get("received_at", "")),
                    "unread": bool(message.get("unread", False)),
                    "flagged": bool(message.get("flagged", False)),
                    "direct": bool(message.get("direct", False)),
                    "due_date": str(message.get("due_date", "")),
                }
            )
        return ConnectorResponse(
            status=CONFIGURED,
            source_id=self.source_id,
            observed_at=str(payload.get("observed_at", "")),
            data={"messages": cleaned},
        )


# ---------------------------------------------------------------------------
# Calendar (read-only)
# ---------------------------------------------------------------------------
class CalendarSource(Connector):
    kind = "calendar"

    @abstractmethod
    def fetch_events(self, day_iso: str) -> ConnectorResponse:
        """Return ``{"events": [...]}`` for one calendar day."""


class NotConfiguredCalendarSource(CalendarSource):
    def __init__(self) -> None:
        super().__init__(source_id="calendar:none", provider="none")

    def fetch_events(self, day_iso: str) -> ConnectorResponse:
        return self.not_configured("No calendar provider is configured.")


class MockCalendarSource(FixtureConnector, CalendarSource):
    kind = "calendar"

    def __init__(self, fixture: Optional[str]) -> None:
        FixtureConnector.__init__(self, "calendar:mock", fixture)

    def fetch_events(self, day_iso: str) -> ConnectorResponse:
        payload, failure = self.load_fixture()
        if failure is not None:
            return failure
        events = payload.get("events", [])
        if not isinstance(events, list):
            return self.failure("The calendar fixture must contain an 'events' list.")
        cleaned = []
        for event in events:
            if not isinstance(event, dict):
                return self.failure("The calendar fixture contains a malformed event.")
            date = str(event.get("date", ""))
            if day_iso and date and date != day_iso:
                continue
            cleaned.append(
                {
                    "id": str(event.get("id", "")),
                    "title": str(event.get("title", "")),
                    "date": date,
                    "start": str(event.get("start", "")),
                    "end": str(event.get("end", "")),
                    "needs_preparation": bool(event.get("needs_preparation", False)),
                    "response_pending": bool(event.get("response_pending", False)),
                }
            )
        cleaned.sort(key=lambda item: (item["date"], item["start"]))
        return ConnectorResponse(
            status=CONFIGURED,
            source_id=self.source_id,
            observed_at=str(payload.get("observed_at", "")),
            data={"events": cleaned},
        )


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------
class TrendsSource(Connector):
    kind = "trends"

    @abstractmethod
    def fetch_snapshot(self, source_ids: tuple[str, ...]) -> ConnectorResponse:
        """Return ``{"items": {source_id: [{"id","title","published_at"}, ...]}}``."""


class NotConfiguredTrendsSource(TrendsSource):
    def __init__(self) -> None:
        super().__init__(source_id="trends:none", provider="none")

    def fetch_snapshot(self, source_ids: tuple[str, ...]) -> ConnectorResponse:
        return self.not_configured("No trends provider is configured.")


class MockTrendsSource(FixtureConnector, TrendsSource):
    kind = "trends"

    def __init__(self, fixture: Optional[str]) -> None:
        FixtureConnector.__init__(self, "trends:mock", fixture)

    def fetch_snapshot(self, source_ids: tuple[str, ...]) -> ConnectorResponse:
        payload, failure = self.load_fixture()
        if failure is not None:
            return failure
        raw_items = payload.get("items", {})
        if not isinstance(raw_items, dict):
            return self.failure("The trends fixture must contain an 'items' object.")
        items: dict[str, list[dict]] = {}
        wanted = set(source_ids) if source_ids else set(raw_items)
        for source_id, entries in raw_items.items():
            if source_id not in wanted:
                continue
            if not isinstance(entries, list):
                return self.failure(f"Trends source '{source_id}' is not a list.")
            cleaned = []
            for entry in entries:
                if not isinstance(entry, dict):
                    return self.failure(f"Trends source '{source_id}' has a malformed entry.")
                cleaned.append(
                    {
                        "id": str(entry.get("id", "")),
                        "title": str(entry.get("title", "")),
                        "published_at": str(entry.get("published_at", "")),
                    }
                )
            cleaned.sort(key=lambda item: item["id"])
            items[str(source_id)] = cleaned
        missing = sorted(wanted - set(items))
        return ConnectorResponse(
            status=CONFIGURED,
            source_id=self.source_id,
            observed_at=str(payload.get("observed_at", "")),
            data={"items": items, "missing_sources": missing},
        )
