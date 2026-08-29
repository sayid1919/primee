"""Discovery and loading of project-local Primee skills.

A skill is a directory containing a ``SKILL.md`` file.  Discovery is a two step
process on purpose:

* **Load time** reads and validates metadata only.  No skill code is imported,
  so listing or inspecting skills can never execute anything.
* **Execution time** imports the handler lazily, and only after proving the
  handler file lives inside the skill's own directory and is not a symlink.

This keeps ``SKILL.md`` a pure data file: it can *name* a handler, but it can
never point Primee at arbitrary code elsewhere on the machine.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .errors import ErrorCode, PrimeeError
from .frontmatter import parse_document
from .manifest import SkillManifest, build_manifest

SKILL_FILE = "SKILL.md"
MAX_SKILLS = 100


@dataclass
class SkillLoadError:
    directory: str
    skill_name: Optional[str]
    error_code: str
    message: str

    def to_dict(self) -> dict:
        return {
            "directory": self.directory,
            "skill_name": self.skill_name,
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass
class LoadedSkill:
    manifest: SkillManifest
    directory: Path
    body: str
    _handler: Optional[Callable] = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.manifest.name

    def handler_path(self) -> Path:
        """Resolve the handler file and prove it stays inside the skill folder."""
        directory = self.directory.resolve()
        candidate = directory / self.manifest.handler_file
        if candidate.is_symlink():
            raise PrimeeError(
                ErrorCode.HANDLER_INVALID,
                f"Handler of skill '{self.name}' is a symbolic link, which is not allowed.",
            )
        resolved = Path(os.path.realpath(candidate))
        try:
            common = os.path.commonpath([str(directory), str(resolved)])
        except ValueError:
            common = ""
        if os.path.normcase(common) != os.path.normcase(str(directory)):
            raise PrimeeError(
                ErrorCode.HANDLER_INVALID,
                f"Handler of skill '{self.name}' resolves outside the skill directory.",
            )
        if not resolved.is_file():
            raise PrimeeError(
                ErrorCode.HANDLER_INVALID,
                f"Handler file '{self.manifest.handler_file}' of skill '{self.name}' was not found.",
            )
        return resolved

    def load_handler(self) -> Callable:
        """Import the handler lazily and return the declared function."""
        if self._handler is not None:
            return self._handler
        path = self.handler_path()
        module_name = f"primee_skill_{self.name}_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise PrimeeError(
                ErrorCode.HANDLER_INVALID,
                f"Handler of skill '{self.name}' could not be prepared for import.",
            )
        module = importlib.util.module_from_spec(spec)
        previous = sys.modules.get(module_name)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            if previous is None:
                sys.modules.pop(module_name, None)
            else:  # pragma: no cover - defensive
                sys.modules[module_name] = previous
            raise PrimeeError(
                ErrorCode.HANDLER_INVALID,
                f"Handler of skill '{self.name}' failed to load.",
                detail={"reason": type(exc).__name__},
            ) from exc
        function = getattr(module, self.manifest.handler_function, None)
        if not callable(function):
            raise PrimeeError(
                ErrorCode.HANDLER_INVALID,
                f"Skill '{self.name}' does not define a callable "
                f"'{self.manifest.handler_function}'.",
            )
        self._handler = function
        return function


@dataclass
class SkillRegistry:
    skills: dict[str, LoadedSkill] = field(default_factory=dict)
    errors: list[SkillLoadError] = field(default_factory=list)
    roots: list[Path] = field(default_factory=list)

    def names(self) -> list[str]:
        return sorted(self.skills)

    def get(self, name: str) -> Optional[LoadedSkill]:
        return self.skills.get(name)

    def require(self, name: str) -> LoadedSkill:
        skill = self.skills.get(name)
        if skill is None:
            raise PrimeeError(
                ErrorCode.SKILL_NOT_FOUND, f"Skill '{name}' is not installed."
            )
        return skill

    def __iter__(self):
        for name in self.names():
            yield self.skills[name]

    def __len__(self) -> int:
        return len(self.skills)


def read_skill_file(directory: Path) -> tuple[SkillManifest, str]:
    """Read and validate one skill directory's SKILL.md."""
    skill_file = directory / SKILL_FILE
    if skill_file.is_symlink():
        raise PrimeeError(
            ErrorCode.MANIFEST_INVALID,
            "SKILL.md is a symbolic link, which is not allowed.",
        )
    try:
        text = skill_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PrimeeError(
            ErrorCode.FRONTMATTER_INVALID, "SKILL.md is not valid UTF-8 text."
        ) from exc
    except OSError as exc:
        raise PrimeeError(
            ErrorCode.MANIFEST_INVALID, "SKILL.md could not be read."
        ) from exc
    metadata, body = parse_document(text)
    manifest = build_manifest(metadata)
    return manifest, body


def discover_skills(roots: list[Path] | Path) -> SkillRegistry:
    """Discover every skill directory beneath one or more roots.

    Directories are visited in a deterministic sorted order so that duplicate
    resolution is reproducible.
    """
    if isinstance(roots, (str, Path)):
        roots = [Path(roots)]
    registry = SkillRegistry(roots=[Path(root) for root in roots])

    for root in registry.roots:
        if not root.is_dir():
            registry.errors.append(
                SkillLoadError(
                    directory=str(root),
                    skill_name=None,
                    error_code=ErrorCode.SKILLS_ROOT_MISSING,
                    message="Skills directory does not exist.",
                )
            )
            continue
        for directory in sorted(
            (p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))),
            key=lambda p: p.name,
        ):
            if not (directory / SKILL_FILE).is_file():
                continue
            if len(registry.skills) >= MAX_SKILLS:
                registry.errors.append(
                    SkillLoadError(
                        directory=str(directory),
                        skill_name=None,
                        error_code=ErrorCode.MANIFEST_INVALID,
                        message="Skill limit reached; this skill was not loaded.",
                    )
                )
                continue
            try:
                manifest, body = read_skill_file(directory)
            except PrimeeError as exc:
                registry.errors.append(
                    SkillLoadError(
                        directory=str(directory),
                        skill_name=None,
                        error_code=exc.code,
                        message=exc.sanitized_message(),
                    )
                )
                continue

            if manifest.name in registry.skills:
                existing = registry.skills[manifest.name].directory
                registry.errors.append(
                    SkillLoadError(
                        directory=str(directory),
                        skill_name=manifest.name,
                        error_code=ErrorCode.DUPLICATE_SKILL_NAME,
                        message=(
                            f"Skill name '{manifest.name}' is already used by "
                            f"'{existing.name}'; this duplicate was rejected."
                        ),
                    )
                )
                continue

            registry.skills[manifest.name] = LoadedSkill(
                manifest=manifest, directory=directory, body=body
            )
    return registry
