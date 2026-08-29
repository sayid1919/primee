---
name: inbox
version: 1.0.0
description: "Prepare a read-only morning brief of at most three things that genuinely need attention today."
triggers:
  - inbox
  - "morning brief"
  - "morning briefing"
  - "what needs my attention"
  - "anything urgent"
  - "check my email"
  - "today's meetings"
  - "خلاصه صبحگاهی"
  - "ایمیل‌ها"
  - "امروز چه خبر"
exclusions:
  - "send email"
  - "reply to"
  - "delete email"
  - "archive"
  - metrics
  - kpi
  - "daily plan"
  - "what changed"
required_permissions:
  - connector.email.read
  - connector.calendar.read
inputs:
  - name: day
    type: date
    required: false
    description: "ISO date to brief on. Defaults to today."
  - name: limit
    type: integer
    required: false
    description: "Maximum items to return, capped at three."
outputs:
  - name: day
    type: string
  - name: items
    type: list
  - name: sources
    type: list
persistence:
  vault_writes: false
  path_prefix: ""
  description: "Inbox is read-only and proposes no Vault writes in Step One."
handler: handler.py:run
---

# Inbox

## What this skill does

Builds a morning brief from read-only email and calendar connectors and returns
**at most three** items, each with an explicit reason for why it needs attention
today. Items are ranked by transparent, inspectable signals, not by a model.

## When it should activate

When the user asks for their morning brief, what needs attention today, whether
anything is urgent, or what is on the calendar today.

## When it must not activate

- For any request to send, reply, delete, archive, move, accept or decline. The
  skill is read-only and its exclusions veto those requests.
- For metrics, daily planning or trend questions.

## What information it requires

An optional ISO date. Everything else comes from the configured connectors.

## What it may read

Message metadata (subject, sender label, timestamp, unread, flagged, direct,
due date) and calendar event metadata. It does not read or store message bodies,
and the audit log never records message content.

## What it may write

Nothing. It proposes no Vault writes and no external actions.

## What requires user approval

Every mutating mail or calendar action, without exception. Step One implements
none of them, and the `email.send` and `calendar.write` permissions are denied by
Primee Core even if a policy tried to allow them.

## What it returns

A standard Primee result. `structured_data` contains the day, the selected
items with a `reason` and a `score_breakdown` for each, and the status of each
data source. When no connector is configured, the result says so plainly and
returns no items rather than inventing a brief.
