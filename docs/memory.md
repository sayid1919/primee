# Primee Memory

Primee's memory is a folder of plain Markdown files on your own computer.

There is no database, no SQLite, no vector store, no proprietary notes format,
and no hosted memory service of any kind. The Markdown **is** the memory. Every
cache Primee builds is reconstructed from those files and can be deleted without
losing anything. If a write did not reach the Vault, Primee does not claim to
remember it.

You can read, edit, grep and back up your memory with any text editor. Obsidian
can display it if you like the graph view, but Primee never requires it and
never writes anything Obsidian-specific.

## The three-folder model

```
<your vault>/
├── raw/          original captured material   — immutable
├── wiki/         distilled knowledge          — maintained
├── outputs/      what Primee produced         — dated, append-only in practice
├── INDEX.md      generated from the pages themselves
├── CHANGELOG.md  append-only record of accepted changes
└── PRIMEE.md     the rules, for humans
```

### `raw/` — what was captured

Transcripts, clips, imported text, notes, observations, data dumps. A raw note
records the captured meaning, its source and the time it was captured.

**A raw note is never rewritten.** This is enforced in code, not by convention:
the memory service has no path that updates or renames a `raw_note`. When
something in a capture turns out to be wrong, Primee writes a separate
*amendment* — a new page that links back to the original and states the
correction. The original file stays byte-for-byte intact, and `backlinks` finds
the amendment from the original.

### `wiki/` — what is understood

One primary page per topic, at a stable slug so links keep working. Wiki pages
are updated as understanding improves; `created` and the page `id` never change,
and `updated` moves only when the content actually changed.

A good wiki page keeps three things apart:

- **Facts** — statements observed in a raw capture, each linked to it.
- **Interpretations** — readings that go beyond what was captured.
- **Open conflicts** — contradictions preserved rather than quietly resolved.

The template Vault demonstrates all three.

### `outputs/` — what Primee produced

Reports, daily plans, briefs, snapshots, drafts. Filenames always begin with an
ISO date: `YYYY-MM-DD-HHMMSS-short-title.md`. A collision gets a `-2`, `-3`
suffix rather than overwriting anything.

An output whose `status` is `final` or `shipped` is **never overwritten**.
Correcting one means publishing a *revision*: a new dated page carrying
`supersedes`, linked from its body. The original file is not touched at all —
not even to add a "superseded" marker — so immutability is literal rather than
a matter of trust. The link is discoverable in both directions through
`backlinks`.

## The page schema

Every page carries YAML frontmatter. Required:

| Field | Rule |
| --- | --- |
| `id` | stable and unique; derived from path, title and creation time |
| `schema_version` | integer, currently `1` |
| `title` | one line |
| `type` | must match the folder the page lives in |
| `tags` | a YAML list, normalised to lowercase and sorted |
| `created` | timezone-aware ISO 8601; **never changes after creation** |
| `updated` | timezone-aware ISO 8601; moves only on a real change |
| `summary` | one short line |

Also supported: `source`, `source_type`, `sensitivity`, `status`, `related`,
`supersedes`, `superseded_by`.

Controlled vocabularies are validated, not merely suggested:

- `type` — `raw_note`, `raw_amendment`, `wiki_topic`, `output_report`,
  `output_plan`, `output_brief`, `output_snapshot`, `output_draft`
- `sensitivity` — `public`, `internal`, `private`, `sensitive`
- `status` — `captured`, `amended`, `draft`, `active`, `review`, `final`,
  `shipped`, `superseded`
- `source_type` — `manual`, `transcript`, `clip`, `import`, `observation`,
  `connector`, `skill`, `unknown`

A page with a missing required field, an unknown type, a naive timestamp, a type
that does not match its folder, or malformed YAML is **rejected with a sanitized
error**. Primee never invents missing metadata to make a page load.

Frontmatter is parsed by the same strict YAML-subset parser Primee uses for
`SKILL.md`, so a Vault page cannot smuggle in a YAML tag, anchor or alias.

## Wikilinks and the knowledge graph

Links are written the way a person writes them:

```markdown
See [[wiki/lighthouse-project]] and [[raw/2026-01-15-090000-kickoff|the capture]].
```

Resolution is plain path matching over the Markdown files. A link resolves
exactly, or by unique page name; if two pages share a name the link is reported
**ambiguous** rather than resolved to a guess.

Primee supports forward links, backlinks, unresolved-link detection,
duplicate-target detection, ambiguity reporting, link validation and safe
renaming.

**Unresolved links are reported, never deleted.** A dangling link usually means
a page you still intend to write, and silently removing it would destroy that
intention.

Renaming stops and asks when it would be ambiguous: if the destination exists,
or if the new name would collide with another page's name and make short links
ambiguous, the rename is refused with an explanation. A rename copies the page
to its new path and repoints every wikilink; the original file is kept, because
Primee has no delete operation anywhere.

## `INDEX.md`

A human-readable table of every page: wikilink, title, type, one-line summary
and last-updated timestamp, grouped by folder and newest first.

It is **generated**, deterministic and byte-stable: the same Vault always
produces the same index. It excludes root files, non-Markdown files and pages
that fail validation (those are reported instead). Delete it and
`rebuild_index` recreates it exactly.

Never keep information in `INDEX.md` that exists nowhere else.

## `CHANGELOG.md`

Append-only. Each accepted change adds one row: timestamp, operation, page id,
path, requesting skill, approval state, sanitized result and a content hash.

Existing rows are never rewritten or deleted. An append first verifies that the
file still begins with Primee's canonical header; if it does not, the append is
refused and Primee says so, rather than quietly starting a new history.

The changelog records *what happened*, never page content, message bodies or
credentials. Every free-text field passes through redaction on the way in.

## Operations

Primee exposes a fixed catalogue of named operations. There is deliberately no
generic "write this file anywhere" call.

| Operation | Permission | Changes anything |
| --- | --- | --- |
| `init` | `vault.init` | yes |
| `validate` | `vault.read` | no |
| `read` | `vault.read` | no |
| `list` | `vault.list` | no |
| `search_text` / `search_tag` / `search_metadata` / `recent` | `vault.list` | no |
| `backlinks` / `validate_links` | `vault.list` | no |
| `create_raw` | `vault.create` | yes |
| `amend_raw` | `vault.create` | yes |
| `write_wiki` | `vault.create` | yes |
| `update_wiki` | `vault.update` | yes |
| `publish_output` | `vault.create` | yes |
| `revise_output` | `vault.create` | yes |
| `rebuild_index` | `vault.index` | yes |
| `append_change` | `vault.append` | yes |
| `create` / `append` / `update` | matching permission | yes |

The mapping lives in one place, `primee/memory/operations.py`, and is used both
by Primee Core (to gate the call) and by the Vault skill (to perform it), so the
gate and the implementation cannot drift apart.

**No delete operation exists at any layer.** Not in the catalogue, not in the
service, not in the storage class.

## What is blocked

Every path is validated for shape and then for resolution:

- `..` traversal, absolute paths, drive letters, `~`, UNC paths
- symbolic links and NTFS junctions (the resolved target must stay in the root)
- invalid filenames, control characters, bidi overrides
- reserved Windows device names (`CON`, `NUL`, `COM1`, `LPT9`, …)
- any write outside the Vault root
- writing `INDEX.md`, `CHANGELOG.md` or `PRIMEE.md` directly
- updating or renaming a raw note
- overwriting an output marked `final` or `shipped`

Absolute or traversing link targets in frontmatter are **rejected**, not
normalised into something that looks acceptable — a link pointing outside the
Vault is a mistake you need to see.

## Skills and memory

The Vault skill is the only gateway. Other skills never touch the filesystem:
they return `proposed_vault_writes`, and Primee Core checks the permission, runs
the approval gate, and hands the proposal to the Vault skill.

For a memory operation a skill supplies only the body and the page metadata. It
does **not** choose the path: `path` must be empty, and the result is rejected
if it is not. The Vault generates the ISO-dated filename and writes the
frontmatter itself, so a skill can neither place a page in the wrong folder nor
fabricate its metadata.

| Skill | Writes |
| --- | --- |
| `metrics` | one `output_report` per run (`store=false` to suppress) |
| `inbox` | one `output_brief` per run (`store=false` to suppress) |
| `trends` | one `output_snapshot` per run; reads the most recent one to diff against |
| `plan` | one `output_plan` per run, filed under the day it is *for* |
| `vault` | everything, on behalf of the others |

Running `plan` twice in a day produces two separately timestamped plans rather
than replacing the first, so the earlier plan and the decision behind it stay on
record.

## Privacy: what Markdown does not give you

**The Vault is not encrypted.** It is plain text. Anyone who can read the folder
can read your memory: another user on the machine, a backup service, a sync
client, anyone with the disk.

Primee does not claim otherwise, and nothing in Primee encrypts anything.
Operating-system file permissions and full-disk encryption such as BitLocker are
**separate layers you have to enable yourself**. They are not implemented here
and are not planned for this step.

What Primee does do: it refuses to write content that looks like a credential —
private key blocks, `key = value` credential assignments, bearer tokens. That
check is a safety net, not a guarantee. Never put a password, API key, OAuth or
session token, cookie, recovery code, private key, seed phrase, PIN, complete
payment-card details, banking login or credential-store export anywhere near
Primee.

## Why your real Vault is not in Git

Your Vault is your notes, your knowledge and your work. Committing it would push
it to a remote server, replicate it to every clone, and preserve it in history
forever — including anything you later delete.

So the real Vault lives outside the repository, at a path you configure, and
`.gitignore` blocks the obvious accidents (`/vault/`, `/PrimeeVault/`, stray
`raw/`, `wiki/` and `outputs/` Markdown, local audit logs, `*.local.toml`,
`.env`, downloaded models, temporary audio).

The repository contains only: implementation code, documentation, the fictional
template Vault under `templates/`, synthetic test fixtures and tests.

## Why `PRIMEE.md` and not `CLAUDE.md`

`PRIMEE.md` describes the Vault's rules for whoever opens the folder. Primee
never creates a `CLAUDE.md` anywhere, and there is no `~/.claude/` file in this
project.

Claude Code was a temporary tool used to help write Primee's source code. It is
not part of Primee, and the Vault belongs to Primee and to you — not to any
development tool that happened to be used along the way.

`PRIMEE.md` *documents* the rules; it does not enforce them. Enforcement lives in
Primee Core and the memory service, so editing that file changes nothing about
what Primee will or will not do.

## Setting up your Vault

```powershell
# 1. See exactly what would be created. Nothing is written.
python run_primee.py vault init --root "%USERPROFILE%\Documents\PrimeeVault" --dry-run

# 2. Create it. vault.init needs approval in the example policy.
python run_primee.py vault init --root "%USERPROFILE%\Documents\PrimeeVault" --approve vault.init

# 3. Point Primee at it, without writing your user name into any file.
setx PRIMEE_VAULT_PATH "%USERPROFILE%\Documents\PrimeeVault"

# 4. Check.
python run_primee.py doctor
python run_primee.py vault validate
```

Initialization refuses unsafe paths (system directories, filesystem roots, your
home directory itself, anything inside a git repository) and refuses to
initialise over a non-empty directory that does not look like a Primee Vault.

## Everyday commands

```powershell
python run_primee.py vault validate                       # structure, schema, links
python run_primee.py vault validate_links                 # dangling and ambiguous links
python run_primee.py vault rebuild_index                   # regenerate INDEX.md
python run_primee.py vault search_text --query "lighthouse"
python run_primee.py vault search_tag  --tag project
python run_primee.py vault search_metadata --field type --value output_plan
python run_primee.py vault recent --limit 10
python run_primee.py vault backlinks --target wiki/lighthouse-project
python run_primee.py vault read --path wiki/lighthouse-project.md
```

## What Step Two does not do

- No encryption at rest.
- No multi-device sync.
- No full-text index beyond substring matching; search reads the files.
- No automatic distillation of raw notes into wiki pages. Primee has no
  reasoning engine, so a human writes the wiki pages.
- No real email, calendar, metrics or web integration; connectors remain
  interfaces plus mocks with synthetic fixtures.
- No scheduling. Nothing runs on a timer.
- No delete or archive operation.

See [roadmap.md](roadmap.md) for the full list of placeholders.
