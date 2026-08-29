"""Inbox skill handler - a read-only morning brief.

Ranking is a small, transparent set of weighted signals.  Every returned item
carries the exact signals that put it there, so the user can audit the brief.
The skill never returns more than three items and never performs an action.
"""

from __future__ import annotations

from typing import Any

from primee.connectors.base import CONFIGURED, DENIED
from primee.core.context import SkillContext
from primee.core.errors import ErrorCode
from primee.core.result_types import SkillResult, VaultWrite

SKILL_NAME = "inbox"
MAX_ITEMS = 3
FETCH_LIMIT = 50

EMAIL_SIGNALS = (
    ("due_today", 5.0, "it has a deadline today"),
    ("flagged", 3.0, "you flagged it"),
    ("direct", 2.0, "it was addressed to you directly"),
    ("unread", 1.0, "it is still unread"),
)
EVENT_SIGNALS = (
    ("response_pending", 4.0, "it is still waiting for your response"),
    ("needs_preparation", 3.0, "it needs preparation beforehand"),
    ("today", 2.0, "it happens today"),
)


def run(context: SkillContext) -> SkillResult:
    day = str(context.input("day", "") or context.today()).strip()
    limit = context.input("limit", MAX_ITEMS)
    try:
        limit = max(1, min(MAX_ITEMS, int(limit)))
    except (TypeError, ValueError):
        return SkillResult.fail(
            SKILL_NAME,
            "The requested item limit is not a number.",
            ErrorCode.INVALID_INPUT,
            "'limit' must be an integer between 1 and 3.",
        )

    email = context.connectors.email()
    calendar = context.connectors.calendar()
    email_response = email.fetch_recent(FETCH_LIMIT)
    calendar_response = calendar.fetch_events(day)

    sources = [
        {"kind": "email", **email_response.describe()},
        {"kind": "calendar", **calendar_response.describe()},
    ]

    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []

    if email_response.status == CONFIGURED:
        candidates.extend(_score_messages((email_response.data or {}).get("messages", []), day))
    else:
        warnings.append(f"Email source unavailable: {email_response.message}")

    if calendar_response.status == CONFIGURED:
        candidates.extend(_score_events((calendar_response.data or {}).get("events", []), day))
    else:
        warnings.append(f"Calendar source unavailable: {calendar_response.message}")

    structured = {
        "day": day,
        "generated_at": context.now_iso(),
        "read_only": True,
        "sources": sources,
        "candidate_count": len(candidates),
        "items": [],
    }

    if email_response.status != CONFIGURED and calendar_response.status != CONFIGURED:
        if DENIED in (email_response.status, calendar_response.status):
            return SkillResult.fail(
                SKILL_NAME,
                "Primee is not permitted to read the email or calendar connector.",
                ErrorCode.PERMISSION_DENIED,
                "The permission policy refuses 'connector.email.read' and "
                "'connector.calendar.read'.",
                structured_data=structured,
                warnings=warnings,
            )
        return SkillResult.fail(
            SKILL_NAME,
            "No email or calendar source is configured, so there is no brief to give.",
            ErrorCode.CONNECTOR_NOT_CONFIGURED,
            "Primee will not invent a morning brief. Configure a read-only email "
            "or calendar connector first.",
            structured_data=structured,
            warnings=warnings,
        )

    candidates.sort(key=lambda item: (-item["score"], item["sort_key"]))
    selected = [item for item in candidates if item["score"] > 0][:limit]
    structured["items"] = [
        {
            "kind": item["kind"],
            "id": item["id"],
            "title": item["title"],
            "when": item["when"],
            "reason": item["reason"],
            "score": round(item["score"], 2),
            "score_breakdown": item["breakdown"],
        }
        for item in selected
    ]

    if not selected:
        summary_line = f"Morning brief for {day}: nothing needs your attention today."
        lines = [summary_line]
    else:
        lines = [f"Morning brief for {day} - {len(selected)} item(s) need attention:"]
        for index, item in enumerate(structured["items"], start=1):
            when = f" ({item['when']})" if item["when"] else ""
            lines.append(f"{index}. [{item['kind']}] {item['title']}{when} - {item['reason']}")
        lines.append("Read-only: Primee has not replied to, moved or changed anything.")

    proposals = []
    if bool(context.input("store", True)):
        proposals.append(
            VaultWrite(
                path="",  # the Vault generates the ISO-dated filename
                operation="publish_output",
                content=_render_brief(day, structured, warnings, context),
                reason="Keep a dated record of the morning brief.",
                metadata={
                    "title": f"Morning brief {day}",
                    "summary": (
                        f"{len(selected)} item(s) needed attention on {day}."
                        if selected
                        else f"Nothing needed attention on {day}."
                    ),
                    "output_type": "output_brief",
                    "tags": ["inbox", "brief", f"day/{day}"],
                    "status": "final",
                    "source": "primee inbox skill",
                },
            )
        )

    return SkillResult.ok(
        SKILL_NAME,
        "\n".join(lines),
        structured_data=structured,
        warnings=warnings,
        proposed_vault_writes=proposals,
    )


def _render_brief(day: str, structured: dict, warnings: list, context) -> str:
    """The Markdown body of the stored brief. Metadata only, never message bodies."""
    lines = [
        f"Prepared by Primee at {context.now_iso()}. Read-only: nothing was sent, "
        "moved, accepted or declined.",
        "",
        "## Needs attention",
        "",
    ]
    if structured["items"]:
        for index, item in enumerate(structured["items"], start=1):
            when = f" — {item['when']}" if item["when"] else ""
            lines.append(f"### {index}. {item['title']}{when}")
            lines.append("")
            lines.append(f"- Source: {item['kind']}")
            lines.append(f"- Why: {item['reason']}")
            lines.append(f"- Signals: {', '.join(sorted(item['score_breakdown'])) or 'none'}")
            lines.append("")
    else:
        lines.extend(["_Nothing needed attention today._", ""])

    lines.extend(["## Sources", ""])
    for source in structured["sources"]:
        lines.append(f"- `{source['kind']}`: {source['status']} ({source['source_id']})")
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    lines.append(
        "> Message bodies are never read or stored. This brief records only "
        "subject lines, event titles and the signals that ranked them."
    )
    return "\n".join(lines) + "\n"


def _score_messages(messages: list[dict], day: str) -> list[dict]:
    scored = []
    for message in messages:
        flags = {
            "due_today": bool(message.get("due_date")) and str(message.get("due_date")) == day,
            "flagged": bool(message.get("flagged")),
            "direct": bool(message.get("direct")),
            "unread": bool(message.get("unread")),
        }
        score, breakdown, reasons = _apply(EMAIL_SIGNALS, flags)
        if score <= 0:
            continue
        scored.append(
            {
                "kind": "email",
                "id": str(message.get("id", "")),
                "title": str(message.get("subject", "(no subject)")),
                "when": str(message.get("received_at", "")),
                "score": score,
                "breakdown": breakdown,
                "reason": "Needs attention because " + ", and ".join(reasons) + ".",
                "sort_key": str(message.get("received_at", "")) + str(message.get("id", "")),
            }
        )
    return scored


def _score_events(events: list[dict], day: str) -> list[dict]:
    scored = []
    for event in events:
        flags = {
            "response_pending": bool(event.get("response_pending")),
            "needs_preparation": bool(event.get("needs_preparation")),
            "today": str(event.get("date", "")) == day,
        }
        score, breakdown, reasons = _apply(EVENT_SIGNALS, flags)
        if score <= 0:
            continue
        scored.append(
            {
                "kind": "calendar",
                "id": str(event.get("id", "")),
                "title": str(event.get("title", "(untitled event)")),
                "when": f"{event.get('date', '')} {event.get('start', '')}".strip(),
                "score": score,
                "breakdown": breakdown,
                "reason": "Needs attention because " + ", and ".join(reasons) + ".",
                "sort_key": str(event.get("start", "")) + str(event.get("id", "")),
            }
        )
    return scored


def _apply(signals, flags) -> tuple[float, dict, list[str]]:
    score = 0.0
    breakdown: dict[str, float] = {}
    reasons: list[str] = []
    for name, weight, phrase in signals:
        if flags.get(name):
            score += weight
            breakdown[name] = weight
            reasons.append(phrase)
    return score, breakdown, reasons
