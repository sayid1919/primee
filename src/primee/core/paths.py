"""Path safety primitives used by the Vault.

Every path that reaches the filesystem is validated twice:

1. :func:`normalize_relative_path` rejects the *shape* of a dangerous path
   (absolute paths, drive letters, ``..`` traversal, control characters,
   Windows reserved device names, over-long components).
2. :func:`resolve_within` rejects the *resolution* of a dangerous path — it
   refuses to follow symlinks or NTFS junctions and requires the fully
   resolved target to remain inside the configured root.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from .errors import PathRejectedError

MAX_PATH_LENGTH = 400
MAX_COMPONENT_LENGTH = 100
MAX_DEPTH = 8

# Reserved DOS device names.  Windows treats "CON.md" the same as "CON".
WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{n}" for n in range(1, 10)]
    + [f"LPT{n}" for n in range(1, 10)]
)

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ILLEGAL_CHARS = set('<>:"|?*\\')

#: Zero-width non-joiner / joiner are ordinary letters in Persian and other
#: scripts, so they are allowed even though Unicode classifies them as "Cf".
#: Every other format character (in particular the bidirectional overrides that
#: can disguise a file extension) stays blocked.
ALLOWED_FORMAT_CHARS = frozenset("\u200c\u200d")


def normalize_relative_path(raw: object) -> str:
    """Validate a vault-relative path and return it in POSIX form.

    Raises :class:`PathRejectedError` for anything unsafe.
    """
    if not isinstance(raw, str):
        raise PathRejectedError("Vault path must be a string.")
    value = unicodedata.normalize("NFC", raw).strip()
    if value == "":
        raise PathRejectedError("Vault path must not be empty.")
    if len(value) > MAX_PATH_LENGTH:
        raise PathRejectedError("Vault path is too long.")
    if "\x00" in value:
        raise PathRejectedError("Vault path contains a NUL byte.")
    if "\\" in value:
        raise PathRejectedError("Vault path must use '/' separators, not '\\'.")
    if value.startswith("/") or value.startswith("~"):
        raise PathRejectedError("Vault path must be relative to the vault root.")
    if _DRIVE_RE.match(value):
        raise PathRejectedError("Vault path must not contain a drive letter.")
    if value.startswith("//") or value.startswith("\\\\"):
        raise PathRejectedError("UNC paths are not allowed.")

    parts = value.split("/")
    if len(parts) > MAX_DEPTH:
        raise PathRejectedError("Vault path is nested too deeply.")

    cleaned: list[str] = []
    for part in parts:
        if part == "":
            raise PathRejectedError("Vault path contains an empty path segment.")
        if part == ".":
            raise PathRejectedError("Vault path must not contain '.' segments.")
        if part == "..":
            raise PathRejectedError("Vault path must not contain '..' traversal.")
        if len(part) > MAX_COMPONENT_LENGTH:
            raise PathRejectedError("Vault path segment is too long.")
        if part.endswith(" ") or part.endswith("."):
            raise PathRejectedError(
                "Vault path segment must not end with a space or a dot."
            )
        for char in part:
            if char in _ILLEGAL_CHARS:
                raise PathRejectedError(
                    f"Vault path segment contains the illegal character {char!r}."
                )
            category = unicodedata.category(char)
            if category in ("Cc", "Cs", "Co", "Cn") or (
                category == "Cf" and char not in ALLOWED_FORMAT_CHARS
            ):
                raise PathRejectedError(
                    "Vault path segment contains a control or invisible character."
                )
        stem = part.split(".")[0].upper()
        if stem in WINDOWS_RESERVED:
            raise PathRejectedError(
                f"Vault path segment '{part}' is a reserved Windows device name."
            )
        try:
            part.encode("utf-8")
        except UnicodeEncodeError as exc:  # pragma: no cover - defensive
            raise PathRejectedError("Vault path is not valid UTF-8.") from exc
        cleaned.append(part)

    return "/".join(cleaned)


def sanitize_component(raw: str, *, fallback: str = "untitled") -> str:
    """Turn arbitrary text into a single safe path component."""
    value = unicodedata.normalize("NFC", str(raw)).strip()
    out = []
    for char in value:
        if char in _ILLEGAL_CHARS or char in "/\\":
            out.append("-")
            continue
        category = unicodedata.category(char)
        if category in ("Cc", "Cs", "Co", "Cn") or (
            category == "Cf" and char not in ALLOWED_FORMAT_CHARS
        ):
            continue
        out.append(char)
    cleaned = "".join(out).strip(" .-")
    cleaned = re.sub(r"[-\s]{2,}", "-", cleaned)
    cleaned = cleaned[:MAX_COMPONENT_LENGTH].strip(" .-")
    if not cleaned or cleaned.split(".")[0].upper() in WINDOWS_RESERVED:
        return fallback
    return cleaned


def resolve_root(root: object) -> Path:
    """Resolve and validate the configured vault root itself."""
    if root is None or (isinstance(root, str) and root.strip() == ""):
        raise PathRejectedError("Vault root is not configured.")
    path = Path(os.path.expanduser(str(root)))
    if not path.is_absolute():
        raise PathRejectedError("Vault root must be an absolute path.")
    try:
        return path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PathRejectedError("Vault root directory does not exist.") from exc


def resolve_within(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root`` and prove it cannot escape.

    Any existing component that is a symlink or junction is rejected outright,
    and the fully resolved target must still live inside ``root``.
    """
    normalized = normalize_relative_path(relative)
    root_real = root.resolve()
    if not root_real.is_dir():
        raise PathRejectedError("Vault root is not a directory.")

    current = root_real
    for part in normalized.split("/"):
        current = current / part
        if current.is_symlink():
            raise PathRejectedError(
                "Vault path passes through a symbolic link, which is not allowed."
            )

    target = root_real.joinpath(*normalized.split("/"))
    resolved = Path(os.path.realpath(target))
    try:
        common = os.path.commonpath([str(root_real), str(resolved)])
    except ValueError as exc:
        raise PathRejectedError("Vault path resolves outside the vault root.") from exc
    if os.path.normcase(common) != os.path.normcase(str(root_real)):
        raise PathRejectedError("Vault path resolves outside the vault root.")
    if os.path.normcase(str(resolved)) == os.path.normcase(str(root_real)):
        raise PathRejectedError("Vault path must point to a file inside the vault.")
    return target
