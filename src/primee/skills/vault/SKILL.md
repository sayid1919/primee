---
name: vault
version: 2.0.0
description: "Primee's only gateway to persistent memory: a plain Markdown Vault of raw captures, wiki knowledge and dated outputs."
triggers:
  - vault
  - memory
  - "vault read"
  - "vault write"
  - "save note"
  - "read note"
  - "rebuild index"
  - "check my links"
  - "یادداشت"
  - "والت"
  - "حافظه"
exclusions:
  - "delete everything"
  - "vault password"
  - "encrypt the vault"
required_permissions:
  - vault.read
  - vault.list
  - vault.create
  - vault.append
  - vault.update
  - vault.init
  - vault.index
inputs:
  - name: operation
    type: string
    required: true
    description: "One of the named Vault operations; there is no generic file write."
  - name: path
    type: string
    required: false
    description: "Vault-relative path, for operations that address an existing page."
  - name: target
    type: string
    required: false
    description: "A wikilink target such as wiki/project-alpha."
  - name: content
    type: string
    required: false
    description: "Markdown body, or file text for the plain file operations."
  - name: metadata
    type: object
    required: false
    description: "Page metadata: title, summary, tags, output_type, source and so on."
  - name: query
    type: string
    required: false
    description: "Search text."
  - name: tag
    type: string
    required: false
    description: "Tag to search for."
  - name: field
    type: string
    required: false
    description: "Frontmatter field for a metadata search."
  - name: value
    type: string
    required: false
    description: "Value for a metadata search."
  - name: limit
    type: integer
    required: false
    description: "Maximum results to return."
  - name: root
    type: string
    required: false
    description: "Approved Vault path, for the init operation only."
  - name: requested_by
    type: string
    required: false
    description: "Name of the skill that asked for this operation."
outputs:
  - name: operation
    type: string
  - name: path
    type: string
  - name: content
    type: string
  - name: metadata
    type: object
  - name: results
    type: list
  - name: paths
    type: list
persistence:
  vault_writes: true
  path_prefix: ""
  description: "Writes anywhere inside the configured Vault root, on behalf of another skill."
handler: handler.py:run
---

# Vault

## What this skill does

Vault is Primee's memory. It owns a single local folder of plain Markdown files
and is the only component in Primee allowed to read or write persistent
content. Every other skill reaches memory through it.

The Vault has three content folders and three root files:

- `raw/` — original captured material. Immutable once accepted.
- `wiki/` — distilled knowledge, one primary page per topic, kept up to date.
- `outputs/` — what Primee produced, filenames beginning with an ISO date.
- `INDEX.md` — generated from the pages' own frontmatter.
- `CHANGELOG.md` — append-only record of accepted changes.
- `PRIMEE.md` — the Vault rules, for humans.

It exposes a fixed list of named operations. There is deliberately **no**
generic "write this file anywhere" call.

## When it should activate

- Another skill proposes a persistent write and Primee Core forwards it here.
- Another skill needs an earlier approved page, such as the previous trends
  snapshot.
- The user asks to save or read a note, search their memory, rebuild the index,
  or check for broken links.

## When it must not activate

- To store a credential of any kind. Content that looks like a key, token,
  password, seed phrase, PIN or recovery code is refused before it is written.
- To read or write anything outside the configured Vault root.
- To delete anything. There is no delete operation at any layer.
- To rewrite an accepted raw note, or an output marked `final` or `shipped`.
- To rewrite `CHANGELOG.md`, or to write `INDEX.md` by hand.

## What information it requires

An `operation`, and whatever that operation needs: a `path` or `target`, a
Markdown `content` body, and a `metadata` mapping carrying at least a `title`
and a `summary` for any page it creates. The Vault root itself comes from user
configuration and is never hardcoded.

## What it may read

Files inside the configured Vault root, and only when the calling skill holds
`vault.read` or `vault.list`.

## What it may write

Files inside the configured Vault root only. Paths are validated twice: once
for shape (no absolute paths, no drive letters, no `..`, no reserved Windows
device names, no control characters) and once for resolution (no symbolic link
or NTFS junction is followed, and the resolved target must remain inside the
root). Writes are atomic.

Three rules are enforced in code, not by convention:

1. A raw note is never rewritten. A correction becomes a separate amendment
   that links back to the original.
2. An output marked `final` or `shipped` is never rewritten. A correction
   becomes a linked revision; the original file is left byte-for-byte intact.
3. `CHANGELOG.md` is only ever appended to, and an append verifies that the
   existing history is still intact first.

## What requires user approval

Whatever the permission policy says. The shipped example policy allows
`vault.read`, `vault.list`, `vault.create`, `vault.append` and `vault.index`
automatically, and requires explicit approval for `vault.update` and
`vault.init`, because one replaces existing content and the other creates a new
folder structure on disk.

## What it returns

A standard Primee result. `structured_data` always carries the `operation` and,
where it applies, the `path` the Vault actually used, the validated `metadata`,
the page `content`, or the `results` of a search. The requesting skill's name is
recorded in every audit event and in every changelog entry.
