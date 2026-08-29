"""Safe local Vault initialization.

Creating a Vault means creating folders and files in a directory the user
named.  That is exactly the kind of operation that goes badly when it guesses,
so this module refuses far more than it accepts:

* the path must be absolute and user-supplied — never a hardcoded personal path;
* system directories, filesystem roots and git working trees are refused;
* a non-empty directory that is not already a Primee Vault is refused;
* a dry run reports precisely what would be created, and creates nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.clock import Clock, timestamp_iso
from ..core.errors import ErrorCode, PrimeeError
from .changelog import initial_content as changelog_content
from .index_builder import build_index
from .schema import (
    CHANGELOG_FILE,
    CONTENT_FOLDERS,
    FORBIDDEN_ROOT_FILES,
    INDEX_FILE,
    PRIMEE_FILE,
)

#: Directories a Vault must never be created in.
_FORBIDDEN_POSIX = (
    "/", "/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/root", "/sbin",
    "/sys", "/usr", "/var", "/tmp", "/opt", "/srv", "/home", "/Users", "/System",
    "/Library", "/Applications",
)
_FORBIDDEN_WINDOWS_SUFFIXES = (
    "windows", "windows/system32", "program files", "program files (x86)",
    "programdata", "$recycle.bin", "system volume information",
)

#: A directory holding only these may still be adopted as a Vault.
_TOLERATED_EXISTING = frozenset(
    {INDEX_FILE, CHANGELOG_FILE, PRIMEE_FILE, ".gitkeep", ".gitignore", "desktop.ini", ".DS_Store"}
)


@dataclass
class InitPlan:
    """What initialization would do, before it does anything."""

    root: str
    directories: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    dry_run: bool = False
    created_directories: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)

    @property
    def creates_anything(self) -> bool:
        return bool(self.directories or self.files)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "dry_run": self.dry_run,
            "will_create_directories": list(self.directories),
            "will_create_files": list(self.files),
            "already_present": list(self.existing),
            "created_directories": list(self.created_directories),
            "created_files": list(self.created_files),
        }

    def describe(self) -> str:
        if not self.creates_anything:
            return f"The Vault at '{self.root}' is already complete; nothing to create."
        verb = "Would create" if self.dry_run else "Created"
        parts = []
        if self.directories:
            parts.append(f"{verb} folder(s): {', '.join(self.directories)}")
        if self.files:
            parts.append(f"{verb} file(s): {', '.join(self.files)}")
        if self.existing:
            parts.append(f"Left untouched: {', '.join(self.existing)}")
        return ". ".join(parts) + "."


def check_path_is_safe(raw_root: object) -> Path:
    """Validate a candidate Vault root and return it resolved."""
    if raw_root is None or not str(raw_root).strip():
        raise PrimeeError(
            ErrorCode.VAULT_NOT_CONFIGURED,
            "No Vault path was given. Primee never chooses a location for your "
            "Vault; set it in your local configuration or pass it explicitly.",
        )
    path = Path(os.path.expanduser(str(raw_root).strip()))
    if not path.is_absolute():
        raise PrimeeError(
            ErrorCode.VAULT_PATH_REJECTED, "The Vault path must be absolute."
        )

    resolved = Path(os.path.normpath(str(path)))
    posix = resolved.as_posix()
    normalized = posix.rstrip("/").casefold() or "/"

    if normalized in {p.rstrip("/").casefold() or "/" for p in _FORBIDDEN_POSIX}:
        raise PrimeeError(
            ErrorCode.VAULT_PATH_REJECTED,
            f"'{posix}' is a system directory and must not hold the Vault.",
        )
    if resolved.parent == resolved:
        raise PrimeeError(
            ErrorCode.VAULT_PATH_REJECTED, "The Vault must not be a filesystem root."
        )
    for suffix in _FORBIDDEN_WINDOWS_SUFFIXES:
        if normalized.endswith("/" + suffix):
            raise PrimeeError(
                ErrorCode.VAULT_PATH_REJECTED,
                f"'{posix}' is a Windows system directory and must not hold the Vault.",
            )
    if resolved == Path.home():
        raise PrimeeError(
            ErrorCode.VAULT_PATH_REJECTED,
            "The Vault must live in its own folder, not directly in your home directory.",
        )
    if resolved.is_symlink():
        raise PrimeeError(
            ErrorCode.VAULT_PATH_REJECTED,
            "The Vault path is a symbolic link, which Primee will not follow.",
        )
    if (resolved / ".git").exists():
        raise PrimeeError(
            ErrorCode.VAULT_PATH_REJECTED,
            "That folder is a git repository. Your personal Vault must live "
            "outside version control.",
        )
    return resolved


def inspect_directory(root: Path) -> list[str]:
    """Return the entries that make a directory unsuitable to adopt as a Vault."""
    if not root.exists():
        return []
    if not root.is_dir():
        raise PrimeeError(
            ErrorCode.VAULT_PATH_REJECTED, "The Vault path exists but is not a directory."
        )
    unexpected = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name in _TOLERATED_EXISTING:
            continue
        if entry.is_dir() and entry.name in CONTENT_FOLDERS:
            continue
        unexpected.append(entry.name)
    return unexpected


def plan_initialization(root: Path) -> InitPlan:
    plan = InitPlan(root=str(root))
    for folder in CONTENT_FOLDERS:
        (plan.existing if (root / folder).is_dir() else plan.directories).append(folder)
    for name in (INDEX_FILE, CHANGELOG_FILE, PRIMEE_FILE):
        (plan.existing if (root / name).is_file() else plan.files).append(name)
    return plan


def initialize_vault(
    raw_root: object,
    clock: Clock,
    *,
    dry_run: bool = False,
    adopt_non_empty: bool = False,
) -> InitPlan:
    """Create the Vault skeleton at ``raw_root``.

    Set ``adopt_non_empty`` only when the user has explicitly confirmed that an
    existing, unrelated-looking directory really is meant to become the Vault.
    """
    root = check_path_is_safe(raw_root)

    unexpected = inspect_directory(root)
    if unexpected and not adopt_non_empty:
        raise PrimeeError(
            ErrorCode.VAULT_PATH_REJECTED,
            f"'{root}' is not empty and does not look like a Primee Vault "
            f"(it contains: {', '.join(unexpected[:6])}"
            f"{', …' if len(unexpected) > 6 else ''}). Primee will not initialise "
            "over an unrelated directory. Choose an empty folder, or confirm "
            "explicitly that this one should be adopted.",
        )

    plan = plan_initialization(root)
    plan.dry_run = dry_run
    if dry_run:
        return plan

    try:
        root.mkdir(parents=True, exist_ok=True)
        for folder in plan.directories:
            (root / folder).mkdir(exist_ok=True)
            plan.created_directories.append(folder)

        now = timestamp_iso(clock)
        contents = {
            INDEX_FILE: build_index([], generated_at=now),
            CHANGELOG_FILE: changelog_content(),
            PRIMEE_FILE: primee_rules_document(now),
        }
        for name in plan.files:
            _atomic_write(root / name, contents[name])
            plan.created_files.append(name)
    except OSError as exc:
        raise PrimeeError(
            ErrorCode.VAULT_IO_ERROR,
            "The Vault could not be created. Check that the folder is writable.",
        ) from exc

    for forbidden in FORBIDDEN_ROOT_FILES:
        if (root / forbidden).exists():
            plan.existing.append(f"{forbidden} (not created by Primee)")
    return plan


def _atomic_write(target: Path, content: str) -> None:
    import tempfile

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".primee-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def primee_rules_document(generated_at: str) -> str:
    """The human-readable rules of the Vault.

    This file explains the rules; it does not enforce them. Enforcement lives in
    Primee Core and the Vault service, so editing this file changes nothing
    about what Primee will or will not do.
    """
    return f"""---
primee_type: vault_rules
generated_at: "{generated_at}"
---

# Primee Vault

This folder is Primee's memory. It is plain Markdown, on your own computer.
You can read, edit, search and back it up with any ordinary text editor.

## Folders

- `raw/` — original captured material: transcripts, clips, imported text,
  notes, observations, data dumps. A raw note records what was captured and
  when. Once accepted it is **not rewritten**: a correction is added as a
  separate amendment that links back to the original.
- `wiki/` — distilled knowledge. One primary page per topic, updated as
  understanding improves, linked back to the raw notes it came from. Facts and
  interpretations are marked separately, and conflicting information is kept
  rather than quietly resolved.
- `outputs/` — what Primee produces: reports, daily plans, briefs, drafts.
  Filenames start with an ISO date. An output marked `final` or `shipped` is
  **not overwritten**: a correction becomes a linked revision.

## Root files

- `INDEX.md` — generated from the pages' own frontmatter. Safe to delete;
  Primee rebuilds it exactly. Never keep anything here that exists nowhere else.
- `CHANGELOG.md` — append-only record of accepted changes. Never rewritten.
- `PRIMEE.md` — this file.

Primee never creates a `CLAUDE.md` here. This Vault belongs to Primee and to
you, not to any development tool that was used to write Primee's code.

## Page format

Every page begins with YAML frontmatter carrying at least `id`,
`schema_version`, `title`, `type`, `tags`, `created`, `updated` and `summary`.
A page with missing or malformed metadata is rejected rather than repaired by
guesswork.

Links are ordinary wikilinks, for example `[[wiki/topic-name]]`. They are
readable in any text editor. Obsidian can display this Vault, but Primee never
requires it.

## What must never be stored here

Passwords, API keys, OAuth or session tokens, cookies, recovery codes, private
keys, seed phrases, PINs, complete payment-card details, banking logins and
credential-store exports. Primee refuses content that looks like a credential,
but that check is a safety net, not a guarantee.

## What this Vault is not

**These files are not encrypted.** Anyone who can read this folder can read
your memory. Operating-system permissions and disk encryption such as BitLocker
are separate protections that Primee does not provide. Do not assume privacy
that has not actually been implemented.

Generated by Primee at {generated_at}.
"""
