# The Vault

The Vault is the only place Primee stores persistent user content, and the Vault
skill is the only component allowed to touch it. No other skill has a storage
handle.

## Where it lives

Configured, never hardcoded:

```toml
[vault]
root = "${env:USERPROFILE}/Documents/PrimeeVault"
max_file_bytes = 524288
```

Rules:

- The real Vault lives **outside** this git repository.
- It is never committed. `.gitignore` blocks `vault/`, `Vault/`, `PrimeeVault/`
  as a second line of defence.
- Cloud sync is off in Step One. The repository holds only code, templates,
  tests and synthetic data.
- The root must be an absolute path that already exists. Primee will not create
  it for you, so a typo cannot silently scatter files somewhere unexpected.

## Format

Markdown with YAML frontmatter, so the Vault stays readable in any editor.
Obsidian can be used as an optional viewer; it is never a dependency.

```markdown
---
primee_type: daily_plan
date: "2026-08-27"
generated_at: "2026-08-27T08:00:00+03:30"
priority_count: 3
---

# Daily plan — 2026-08-27
...
```

## Path isolation

Every path is validated twice.

**Shape** (`normalize_relative_path`) rejects:

| Rejected | Example |
| --- | --- |
| absolute paths | `/etc/passwd` |
| drive letters | `C:/Windows/system.ini` |
| home shortcuts | `~/notes.md` |
| UNC paths | `//server/share/x` |
| parent traversal | `../secret.md`, `a/../../b.md` |
| `.` segments | `a/./b.md` |
| backslash separators | `plans\today.md` |
| Windows reserved device names | `CON`, `nul.md`, `COM1.txt`, `LPT9.md` |
| trailing dot or space in a segment | `notes./x.md`, `file.` |
| control characters, NUL, bidi overrides | `bad\u202egnp.md` |
| over-long paths or segments, depth beyond 8 | |

Persian filenames work normally, including the zero-width non-joiner, which is a
real letter in Persian and is explicitly allowed while every other invisible
formatting character stays blocked.

**Resolution** (`resolve_within`) additionally:

- walks every existing component and refuses any that is a symbolic link;
- resolves the final target with `os.path.realpath`, which also follows NTFS
  junctions, and requires the result to remain inside the Vault root;
- refuses a path that resolves to the root itself.

## Operations

| Operation | Permission | Behaviour |
| --- | --- | --- |
| `read` | `vault.read` | returns UTF-8 text; missing file → `VAULT_FILE_MISSING` |
| `list` | `vault.list` | lists files under an optional prefix, never outside the root |
| `create` | `vault.create` | refuses to overwrite → `VAULT_FILE_EXISTS` |
| `append` | `vault.append` | adds a newline between blocks; creates the file if absent |
| `update` | `vault.update` | requires the file to exist |

**There is no delete operation.** Step One has no way to remove a Vault file at
all — not through the skill, not through the storage layer.

## Atomic writes

Content is written to a temporary file in the same directory, flushed,
`fsync`-ed, then moved into place with `os.replace`, which is atomic on NTFS.
A rejected or failed write leaves the previous content intact and removes the
temporary file.

## Credentials never enter the Vault

Before any write, content is scanned for private key blocks, `key = value`
credential assignments and bearer tokens. A match is refused with
`VAULT_CONTENT_REJECTED` and nothing is written — the content is not "redacted
and stored", it is rejected.

## Every write records who asked

Skills do not write. They return `proposed_vault_writes`, and Primee Core
forwards each one to the Vault skill with the originating skill's name. Both the
result and the audit event record `requested_by`, and the audit event records
the path, the operation and the byte count — never the content.

```
python run_primee.py run "daily plan" --input candidates=...
  vault create plans/2026-08-27.md: written - Vault create on 'plans/2026-08-27.md' completed (612 bytes) for skill 'plan'.
```
