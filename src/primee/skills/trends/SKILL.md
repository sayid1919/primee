---
name: trends
version: 1.0.0
description: "Compare configured sources against the previous approved snapshot and report only what actually changed."
triggers:
  - trends
  - "what changed"
  - "what is new"
  - "any news"
  - "new since yesterday"
  - "competitor changes"
  - "روندها"
  - "چه چیزی تغییر کرده"
  - "خبر جدید"
exclusions:
  - inbox
  - "morning brief"
  - metrics
  - kpi
  - "daily plan"
  - "browse the web"
required_permissions:
  - connector.trends.read
  - vault.read
  - vault.create
  - vault.update
inputs:
  - name: sources
    type: list
    required: false
    description: "Restrict the run to a subset of configured source identifiers."
  - name: snapshot_path
    type: string
    required: false
    description: "Override the Vault path of the stored snapshot."
outputs:
  - name: changes
    type: list
  - name: verified_changes
    type: list
  - name: interpretation
    type: list
  - name: sources
    type: list
persistence:
  vault_writes: true
  path_prefix: trends
  description: "Proposes a snapshot file under trends/ so the next run has something to compare against."
handler: handler.py:run
---

# Trends

## What this skill does

Fetches the current state of configured sources through a read-only connector,
compares it with the previous snapshot stored in the Vault, and reports only
what changed since that snapshot. It then proposes storing the new snapshot so
the next run has a baseline.

## When it should activate

When the user asks what changed, what is new, whether there is news, or how a
watched source or competitor has moved since the last run.

## When it must not activate

- For morning-brief, metrics or planning questions.
- For open-ended web browsing. Step One performs no network access at all: the
  connector interface exists, and only a fixture-backed mock is shipped.

## What information it requires

The list of source identifiers to watch, from `trends.sources` in the user's
configuration, and the previous snapshot from the Vault.

## What it may read

The configured trends connector, and the snapshot file under `trends/` in the
Vault.

## What it may write

One snapshot file under `trends/` in the Vault, and only by proposing the write
to Primee Core, which forwards it to the Vault skill.

## What requires user approval

Whatever the permission policy assigns to `vault.create` and `vault.update`. The
example policy allows creating the first snapshot automatically and requires
approval to overwrite an existing one.

## What it returns

A standard Primee result that keeps two lists strictly apart:

- `verified_changes` - differences Primee actually observed between two
  snapshots: items added, items removed, and fields that changed value.
- `interpretation` - what those changes might mean. In Step One this list is
  always empty, because Primee has no reasoning engine yet and will not
  speculate. It exists so later steps have a place to put inference that is
  clearly separated from observation.

Every change records its source identifier and the observation time of both
snapshots.
