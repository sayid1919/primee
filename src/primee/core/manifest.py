"""Validation of SKILL.md metadata into a typed, immutable manifest.

The manifest is the security boundary between an untrusted ``SKILL.md`` file and
Primee Core.  Anything not described here is ignored; anything malformed is
rejected with a precise message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .errors import ManifestError
from .permissions import PERMISSIONS, SKILL_REQUESTABLE

REQUIRED_FIELDS = (
    "name",
    "version",
    "description",
    "triggers",
    "exclusions",
    "required_permissions",
    "inputs",
    "outputs",
    "persistence",
    "handler",
)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")
HANDLER_RE = re.compile(r"^([a-z_][a-z0-9_]{0,63}\.py):([a-z_][a-z0-9_]{0,63})$")
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

KNOWN_IO_TYPES = frozenset({"string", "integer", "number", "boolean", "date", "list", "object"})
MAX_TRIGGERS = 40
MAX_EXCLUSIONS = 40
MAX_IO = 20
MAX_DESCRIPTION = 500
MAX_TRIGGER_LENGTH = 120


@dataclass(frozen=True)
class IOField:
    name: str
    type: str
    required: bool = False
    description: str = ""


@dataclass(frozen=True)
class Persistence:
    vault_writes: bool = False
    path_prefix: str = ""
    description: str = ""


@dataclass(frozen=True)
class SkillManifest:
    name: str
    version: str
    description: str
    triggers: tuple[str, ...]
    exclusions: tuple[str, ...]
    required_permissions: frozenset[str]
    inputs: tuple[IOField, ...]
    outputs: tuple[IOField, ...]
    persistence: Persistence
    handler_file: str
    handler_function: str
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def handler(self) -> str:
        return f"{self.handler_file}:{self.handler_function}"

    def declares(self, permission: str) -> bool:
        return permission in self.required_permissions

    def describe(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "triggers": list(self.triggers),
            "exclusions": list(self.exclusions),
            "required_permissions": sorted(self.required_permissions),
            "inputs": [
                {"name": f.name, "type": f.type, "required": f.required}
                for f in self.inputs
            ],
            "outputs": [{"name": f.name, "type": f.type} for f in self.outputs],
            "persistence": {
                "vault_writes": self.persistence.vault_writes,
                "path_prefix": self.persistence.path_prefix,
            },
            "handler": self.handler,
        }


def build_manifest(metadata: Mapping[str, Any]) -> SkillManifest:
    """Validate raw frontmatter and return an immutable manifest."""
    if not isinstance(metadata, Mapping):
        raise ManifestError("SKILL.md frontmatter must be a mapping.")

    missing = [name for name in REQUIRED_FIELDS if name not in metadata]
    if missing:
        raise ManifestError(
            "SKILL.md is missing required field(s): " + ", ".join(sorted(missing))
        )

    name = _require_text(metadata["name"], "name", 32)
    if not NAME_RE.match(name):
        raise ManifestError(
            "Field 'name' must be lowercase letters, digits and underscores, "
            "starting with a letter."
        )

    version = _require_text(metadata["version"], "version", 16)
    if not VERSION_RE.match(version):
        raise ManifestError("Field 'version' must look like '1.0.0'.")

    description = _require_text(metadata["description"], "description", MAX_DESCRIPTION)

    triggers = _phrase_list(metadata["triggers"], "triggers", MAX_TRIGGERS, minimum=1)
    exclusions = _phrase_list(metadata["exclusions"], "exclusions", MAX_EXCLUSIONS, minimum=0)

    permissions = _permission_set(metadata["required_permissions"])
    inputs = _io_list(metadata["inputs"], "inputs")
    outputs = _io_list(metadata["outputs"], "outputs")
    persistence = _persistence(metadata["persistence"], permissions)

    handler_raw = _require_text(metadata["handler"], "handler", 140)
    match = HANDLER_RE.match(handler_raw)
    if match is None:
        raise ManifestError(
            "Field 'handler' must look like 'handler.py:run' and reference a "
            "Python file inside the skill's own directory."
        )
    handler_file, handler_function = match.group(1), match.group(2)

    extra = {
        key: value for key, value in metadata.items() if key not in REQUIRED_FIELDS
    }

    return SkillManifest(
        name=name,
        version=version,
        description=description,
        triggers=triggers,
        exclusions=exclusions,
        required_permissions=permissions,
        inputs=inputs,
        outputs=outputs,
        persistence=persistence,
        handler_file=handler_file,
        handler_function=handler_function,
        extra=extra,
    )


def _require_text(value: Any, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"Field {field_name!r} must be a string.")
    text = value.strip()
    if not text:
        raise ManifestError(f"Field {field_name!r} must not be empty.")
    if len(text) > max_length:
        raise ManifestError(f"Field {field_name!r} is longer than {max_length} characters.")
    return text


def _phrase_list(value: Any, field_name: str, maximum: int, *, minimum: int) -> tuple[str, ...]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ManifestError(f"Field {field_name!r} must be a list.")
    phrases: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ManifestError(f"Field {field_name!r} must contain only strings.")
        phrase = " ".join(item.split()).strip()
        if not phrase:
            raise ManifestError(f"Field {field_name!r} must not contain empty phrases.")
        if len(phrase) > MAX_TRIGGER_LENGTH:
            raise ManifestError(f"Field {field_name!r} contains a phrase that is too long.")
        if phrase in phrases:
            raise ManifestError(f"Field {field_name!r} contains the duplicate phrase {phrase!r}.")
        phrases.append(phrase)
    if len(phrases) < minimum:
        raise ManifestError(f"Field {field_name!r} needs at least {minimum} entry.")
    if len(phrases) > maximum:
        raise ManifestError(f"Field {field_name!r} has more than {maximum} entries.")
    return tuple(phrases)


def _permission_set(value: Any) -> frozenset[str]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ManifestError("Field 'required_permissions' must be a list.")
    names: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ManifestError("Field 'required_permissions' must contain only strings.")
        permission = item.strip()
        if permission not in PERMISSIONS:
            raise ManifestError(
                f"Unknown permission {permission!r} in 'required_permissions'."
            )
        if permission not in SKILL_REQUESTABLE:
            raise ManifestError(
                f"Permission {permission!r} is reserved for Primee Core and cannot "
                "be requested by a skill."
            )
        names.add(permission)
    return frozenset(names)


def _io_list(value: Any, field_name: str) -> tuple[IOField, ...]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ManifestError(f"Field {field_name!r} must be a list.")
    if len(value) > MAX_IO:
        raise ManifestError(f"Field {field_name!r} has too many entries.")
    fields: list[IOField] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ManifestError(
                f"Every entry of {field_name!r} must be a mapping with 'name' and 'type'."
            )
        name = _require_text(item.get("name"), f"{field_name}.name", 32)
        if not FIELD_NAME_RE.match(name):
            raise ManifestError(
                f"Entry name {name!r} in {field_name!r} must be a lowercase identifier."
            )
        if name in seen:
            raise ManifestError(f"Field {field_name!r} declares {name!r} twice.")
        seen.add(name)
        type_name = _require_text(item.get("type"), f"{field_name}.type", 16)
        if type_name not in KNOWN_IO_TYPES:
            raise ManifestError(
                f"Unknown type {type_name!r} in {field_name!r}; allowed types are "
                + ", ".join(sorted(KNOWN_IO_TYPES))
            )
        required = item.get("required", False)
        if not isinstance(required, bool):
            raise ManifestError(f"Field {field_name}.required must be true or false.")
        description = item.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ManifestError(f"Field {field_name}.description must be a string.")
        fields.append(
            IOField(name=name, type=type_name, required=required, description=description.strip())
        )
    return tuple(fields)


def _persistence(value: Any, permissions: frozenset[str]) -> Persistence:
    if not isinstance(value, Mapping):
        raise ManifestError("Field 'persistence' must be a mapping.")
    vault_writes = value.get("vault_writes", False)
    if not isinstance(vault_writes, bool):
        raise ManifestError("Field 'persistence.vault_writes' must be true or false.")
    path_prefix = value.get("path_prefix", "") or ""
    if not isinstance(path_prefix, str):
        raise ManifestError("Field 'persistence.path_prefix' must be a string.")
    path_prefix = path_prefix.strip().strip("/")
    description = value.get("description", "") or ""
    if not isinstance(description, str):
        raise ManifestError("Field 'persistence.description' must be a string.")

    write_permissions = {"vault.create", "vault.append", "vault.update"}
    if vault_writes and not (permissions & write_permissions):
        raise ManifestError(
            "Field 'persistence.vault_writes' is true but the skill declares no "
            "vault write permission."
        )
    if not vault_writes and (permissions & write_permissions):
        raise ManifestError(
            "The skill declares a vault write permission but "
            "'persistence.vault_writes' is false."
        )
    return Persistence(
        vault_writes=vault_writes, path_prefix=path_prefix, description=description.strip()
    )
