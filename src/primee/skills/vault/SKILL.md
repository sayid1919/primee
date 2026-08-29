---
name: vault
version: 1.0.0
description: "The only gateway for reading and writing Primee's persistent notes and skill output."
triggers:
  - vault
  - "vault read"
  - "vault write"
  - "save note"
  - "read note"
  - "یادداشت"
  - "والت"
exclusions:
  - "delete everything"
  - "vault password"
required_permissions:
  - vault.read
  - vault.list
  - vault.create
  - vault.append
  - vault.update
inputs:
  - name: operation
    type: string
    required: true
    description: "One of read, list, create, append, update."
  - name: path
    type: string
    required: false
    description: "Vault-relative path such as plans/2026-08-27.md."
  - name: prefix
    type: string
    required: false
    description: "Vault-relative folder for the list operation."
  - name: content
    type: string
    required: false
    description: "UTF-8 text for create, append and update."
  - name: requested_by
    type: string
    required: false
    description: "Name of the skill that asked for this write."
outputs:
  - name: operation
    type: string
  - name: path
    type: string
  - name: content
    type: string
  - name: paths
    type: list
  - name: bytes_written
    type: integer
persistence:
  vault_writes: true
  path_prefix: ""
  description: "Writes anywhere inside the configured Vault root, on behalf of another skill."
handler: handler.py:run
---

# Vault

## What this skill does

Vault is Primee's storage gateway. It is the only component allowed to read or
write persistent user content, and every other skill reaches storage through it.
It supports five separately permissioned operations: `read`, `list`, `create`,
`append` and `update`.

## When it should activate

- Another skill proposes a persistent write and Primee Core forwards it here.
- Another skill needs to read an earlier approved file, such as yesterday's
  trends snapshot.
- The user explicitly asks to read or save a note.

## When it must not activate

- To store credentials of any kind. Content that looks like a key, token,
  password, seed phrase or recovery code is rejected before it is written.
- To read or write anything outside the configured Vault root.
- To delete files. Step One has no delete operation at all.

## What information it requires

An `operation`, and depending on the operation a `path`, a `prefix` or `content`.
The Vault root itself comes from user configuration and is never hardcoded.

## What it may read

Files inside the configured Vault root only, and only when the calling skill
holds `vault.read`.

## What it may write

Files inside the configured Vault root only. Paths are validated twice: once for
shape (no absolute paths, no drive letters, no `..`, no reserved Windows device
names, no control characters) and once for resolution (no symbolic link or NTFS
junction may be followed, and the resolved target must stay inside the root).
Writes are atomic: content goes to a temporary file in the same directory and is
then moved into place.

## What requires user approval

Whatever the permission policy says. The shipped example policy allows
`vault.read`, `vault.list`, `vault.create` and `vault.append` automatically, and
requires explicit approval for `vault.update`, because updating replaces content
that already exists.

## What it returns

A standard Primee result. `structured_data` carries `operation`, `path`, and then
`content` for reads, `paths` for lists, or `bytes_written` for writes. The
requesting skill's name is recorded in every audit event.
