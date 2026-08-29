---
name: metrics
version: 1.0.0
description: "Summarise explicitly configured numeric series from replaceable read-only connectors."
triggers:
  - metrics
  - "metrics report"
  - kpi
  - "kpi report"
  - "how are the numbers"
  - "website visits"
  - conversion
  - "متریک"
  - "گزارش اعداد"
  - "آمار سایت"
exclusions:
  - "email"
  - "inbox"
  - "calendar"
  - "daily plan"
  - "what changed"
required_permissions:
  - connector.metrics.read
inputs:
  - name: period
    type: string
    required: false
    description: "day, week or month. Defaults to day."
  - name: series
    type: string
    required: false
    description: "Restrict the report to one configured series key."
outputs:
  - name: period
    type: string
  - name: series
    type: list
  - name: unconfigured
    type: list
persistence:
  vault_writes: false
  path_prefix: ""
  description: "Metrics is read-only and proposes no Vault writes in Step One."
handler: handler.py:run
---

# Metrics

## What this skill does

Reads numeric series through a replaceable metrics connector and computes only
the summaries the user configured for each series: `latest`, `sum`, `average`,
`minimum`, `maximum` and `delta`. It returns a short report plus the structured
values behind it.

## When it should activate

When the user asks about their configured numbers: website visits, leads,
conversion rate, orders, revenue trend, open or overdue tasks, or daily, weekly
and monthly performance.

## When it must not activate

- For email, calendar, daily planning or trend-change questions. Other skills
  own those.
- When no metrics connector is configured. In that case it reports
  `not_configured` and stops.

## What information it requires

The series to report on come from `[[metrics.series]]` in the user's own
configuration. Nothing is discovered automatically and nothing is assumed.

## What it may read

Only the configured metrics connector, read-only. Step One ships a
not-configured default and a mock connector that reads a synthetic JSON fixture.
No real account is contacted.

## What it may write

Nothing. Metrics proposes no Vault writes and no external actions.

## What requires user approval

Nothing in Step One, because the skill has no side effects. Connecting a real
metrics provider is a separate, explicitly approved step.

## What it returns

A standard Primee result whose `structured_data` lists every configured series
with its computed summaries, and separately lists every series whose data source
was unavailable. Missing numbers are reported as missing. They are never
estimated, interpolated or invented.
