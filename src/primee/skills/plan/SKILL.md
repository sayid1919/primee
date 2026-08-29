---
name: plan
version: 1.0.0
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
  - vault.update
inputs:
  - name: day
    type: date
    required: false
    description: "ISO date for the plan. Defaults to today."
  - name: candidates
    type: list
    required: false
    description: "Approved candidate actions, each with title, reason, expected_outcome and completion_condition."
  - name: overwrite
    type: boolean
    required: false
    description: "Replace an existing plan for that date instead of refusing."
outputs:
  - name: day
    type: string
  - name: priorities
    type: list
  - name: plan_path
    type: string
persistence:
  vault_writes: true
  path_prefix: plans
  description: "Proposes plans/<ISO-date>.md; the Vault skill performs the write."
handler: handler.py:run
---

# Plan

## What this skill does

Turns approved candidate actions into today's three highest priorities. Every
priority carries three things that make it checkable:

- **reason** - why it is a priority today,
- **expected outcome** - what will be true when it is done,
- **completion condition** - the observable test for "finished".

It then proposes writing the plan to `plans/<ISO-date>.md` through the Vault.

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
candidates file in the Vault (`plans/candidates.md`). Each candidate must
already carry a title, a reason, an expected outcome and a completion condition;
the skill will not fabricate any of them.

## What it may read

`plans/candidates.md` and any existing plan for the requested date, both through
the Vault skill.

## What it may write

Exactly one file: `plans/<ISO-date>.md`. It never writes directly. It proposes
the write and Primee Core decides, checking `vault.create` or `vault.update`
against the permission policy first.

## What requires user approval

Overwriting a plan that already exists for that date. The skill refuses by
default and only proposes an `update` when the caller passes `overwrite: true`,
which still has to clear the `vault.update` permission and its approval gate.

## What it returns

A standard Primee result whose `structured_data` holds the day, the ISO plan
path, and the priorities with their reason, expected outcome and completion
condition. The proposed Vault write is returned as a proposal, never as a
completed action.
