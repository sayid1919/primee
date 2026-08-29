---
name: plan
version: 2.0.0
description: "Produce today's three highest-priority actions from approved information, or say plainly that there is not enough."
triggers:
  - plan
  - "daily plan"
  - "today's plan"
  - "plan my day"
  - "top three"
  - "what should i do today"
  - "priorities"
  - "برنامه روزانه"
  - "برنامه امروز"
  - "اولویت‌ها"
exclusions:
  - inbox
  - "morning brief"
  - metrics
  - kpi
  - trends
  - "what changed"
required_permissions:
  - vault.read
  - vault.list
  - vault.create
inputs:
  - name: day
    type: date
    required: false
    description: "ISO date for the plan. Defaults to today."
  - name: candidates
    type: list
    required: false
    description: "Approved candidate actions, each with title, reason, expected_outcome and completion_condition."
  - name: candidates_page
    type: string
    required: false
    description: "Vault page holding approved candidate actions."
outputs:
  - name: day
    type: string
  - name: priorities
    type: list
  - name: plan_path
    type: string
persistence:
  vault_writes: true
  path_prefix: outputs
  description: "Proposes one dated plan in outputs/; the Vault skill performs the write."
handler: handler.py:run
---

# Plan

## What this skill does

Turns approved candidate actions into today's three highest priorities. Every
priority carries three things that make it checkable:

- **reason** - why it is a priority today,
- **expected outcome** - what will be true when it is done,
- **completion condition** - the observable test for "finished".

It then proposes writing the plan to `outputs/`, where the Vault gives it a
filename beginning with the ISO date.

## When it should activate

When the user asks for a daily plan, today's priorities, or what they should
work on today.

## When it must not activate

- For inbox, metrics or trend questions.
- When there is not enough approved information. In that case the skill returns
  `INSUFFICIENT_INFORMATION` and no plan. It does not invent priorities, and it
  does not pad a short list to reach three.

## What information it requires

Candidate actions, supplied either as the `candidates` input or from an approved
candidates page in the Vault (`wiki/plan-candidates` by default). Each candidate
must already carry a title, a reason, an expected outcome and a completion
condition; the skill will not fabricate any of them.

## What it may read

The candidates page, through the Vault skill.

## What it may write

Exactly one new dated plan page in `outputs/`. It never writes directly, never
chooses the filename and never writes the frontmatter: it proposes the write and
Primee Core forwards it to the Vault, which does all three.

## What requires user approval

Whatever the permission policy assigns to `vault.create`. Plan never needs
`vault.update`, because it never rewrites an existing plan. Running it twice in
one day produces a second, separately timestamped plan rather than replacing the
first, so the earlier plan and the decision behind it stay on record.

## What it returns

A standard Primee result whose `structured_data` holds the day and the
priorities with their reason, expected outcome and completion condition. The
proposed Vault write is returned as a proposal, never as a completed action; the
Vault reports the ISO-dated path it actually used.
