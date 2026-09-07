"""The pinned voice manifest: what may be installed, and how it is checked.

A manifest is produced on the Windows computer by
``tools/voice/Get-PrimeeVoiceManifest.ps1`` from *public metadata only*, and
reviewed by the person before anything is downloaded. Afterwards Primee uses it
to prove the files on disk are still the files that were approved.

Two hash kinds are kept strictly apart:

``publisher-sha256``
    SHA-256 published by the distributor (PyPI for wheels, Hugging Face LFS
    metadata for large model files).
``publisher-git-blob-sha1``
    The Git blob id Hugging Face publishes for small, non-LFS files. It is
    ``sha1("blob <size>\\0" + content)`` and is recomputed locally.

A matching hash proves the bytes are the bytes the publisher indexed. It is a
content match, not a security guarantee and not an independent signature.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from ..core.errors import ErrorCode, VoiceError
from ..core.paths import normalize_relative_path

SCHEMA_NAME = "primee-voice-manifest"
SCHEMA_VERSION = 1

HASH_TYPES = {
    "publisher-sha256": 64,
    "publisher-git-blob-sha1": 40,
    "computed-sha256-at-manifest-time": 64,
    "none": 0,
}
COMPONENT_KINDS = frozenset({"runtime", "model", "provenance"})
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_FILES_PER_COMPONENT = 2000
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
_HEX_RE = re.compile(r"^[0-9a-f]+$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ManifestFile:
    path: str
    size: int
    hash_type: str
    hash: str
    url: Optional[str] = None
    hash_source: str = ""

    @property
    def verifiable(self) -> bool:
        return self.hash_type != "none"


@dataclass(frozen=True)
class ManifestComponent:
    component: str
    name: str
    version: Optional[str]
    revision: Optional[str]
    source: str
    license: Optional[str]
    license_note: str
    files: tuple[ManifestFile, ...]
    requires: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def file(self, path: str) -> Optional[ManifestFile]:
        for entry in self.files:
            if entry.path == path:
                return entry
        return None


@dataclass(frozen=True)
class VoiceManifest:
    profile: str
    generated_at: str
    generated_by: str
    components: tuple[ManifestComponent, ...]
    statement: str
    hash_note: str

    def component(self, kind: str, name: Optional[str] = None) -> Optional[ManifestComponent]:
        for entry in self.components:
            if entry.component == kind and (name is None or entry.name == name):
                return entry
        return None

    @property
    def model(self) -> Optional[ManifestComponent]:
        return self.component("model")

    def runtimes(self) -> tuple[ManifestComponent, ...]:
        return tuple(c for c in self.components if c.component == "runtime")

    def describe(self) -> dict:
        return {
            "profile": self.profile,
            "generated_at": self.generated_at,
            "generated_by": self.generated_by,
            "components": [
                {
                    "component": c.component,
                    "name": c.name,
                    "version": c.version,
                    "revision": c.revision,
                    "license": c.license,
                    "files": len(c.files),
                    "verifiable_files": sum(1 for f in c.files if f.verifiable),
                }
                for c in self.components
            ],
        }


# -- loading ----------------------------------------------------------------
def load_manifest(path: Path) -> VoiceManifest:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Manifest file is too large.")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VoiceError(
            ErrorCode.VOICE_MANIFEST_INVALID, "Manifest file does not exist."
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceError(
            ErrorCode.VOICE_MANIFEST_INVALID, "Manifest file could not be read as JSON."
        ) from exc
    return parse_manifest(raw)


def parse_manifest(raw: Any) -> VoiceManifest:
    if not isinstance(raw, dict):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Manifest must be a JSON object.")
    if raw.get("schema") != SCHEMA_NAME:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Manifest schema name is not recognised.")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Manifest schema version is not supported.")

    profile = _text(raw, "profile", 1, 64)
    if not re.match(r"^[a-z][a-z0-9_-]*$", profile):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Manifest profile must be a lowercase identifier.")

    components_raw = raw.get("components")
    if not isinstance(components_raw, list) or not components_raw:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Manifest needs a non-empty components list.")
    if len(components_raw) > 32:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Manifest has too many components.")

    components = tuple(_component(entry) for entry in components_raw)
    if sum(1 for c in components if c.component == "model") != 1:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Manifest must describe exactly one model.")
    if not any(c.component == "runtime" for c in components):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Manifest must describe at least one runtime component.")

    return VoiceManifest(
        profile=profile,
        generated_at=_text(raw, "generated_at", 1, 64),
        generated_by=_text(raw, "generated_by", 1, 128),
        components=components,
        statement=_text(raw, "statement", 1, 1000),
        hash_note=_text(raw, "hash_note", 1, 1000),
    )


def _component(raw: Any) -> ManifestComponent:
    if not isinstance(raw, dict):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Every component must be an object.")
    kind = _text(raw, "component", 1, 32)
    if kind not in COMPONENT_KINDS:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Unknown component kind {kind!r}.")
    name = _text(raw, "name", 1, 128)
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", name):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Component name contains unexpected characters.")

    version = raw.get("version")
    if version is not None:
        version = str(version).strip()
        if not _VERSION_RE.match(version):
            raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Component {name} has a malformed version.")
    revision = raw.get("revision")
    if revision is not None:
        revision = str(revision).strip().lower()
        if not _SHA_RE.match(revision):
            raise VoiceError(
                ErrorCode.VOICE_MANIFEST_INVALID,
                f"Component {name} must be pinned to a full 40-character commit id.",
            )
    if kind == "model" and revision is None:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "The model component must carry a pinned revision.")
    if kind == "runtime" and version is None:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "A runtime component must carry an exact version.")

    source = _text(raw, "source", 1, 512)
    if not source.startswith("https://"):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Component source must be an https URL.")

    license_value = raw.get("license")
    if license_value is not None:
        license_value = str(license_value).strip() or None
        if license_value is not None and len(license_value) > 128:
            raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "Licence identifier is too long.")
    license_note = str(raw.get("license_note", "")).strip()[:1000]

    files_raw = raw.get("files", [])
    if not isinstance(files_raw, list):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Component {name} files must be a list.")
    if len(files_raw) > MAX_FILES_PER_COMPONENT:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Component {name} lists too many files.")
    files = tuple(_file(entry, name) for entry in files_raw)
    seen: set[str] = set()
    for entry in files:
        if entry.path in seen:
            raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Component {name} lists {entry.path} twice.")
        seen.add(entry.path)
    if kind in ("runtime", "model") and not files:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Component {name} lists no files.")

    requires_raw = raw.get("requires", [])
    if not isinstance(requires_raw, list) or not all(isinstance(item, str) for item in requires_raw):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Component {name} requires must be a list of strings.")

    extra = {
        key: value
        for key, value in raw.items()
        if key not in ("component", "name", "version", "revision", "source", "license", "license_note", "files", "requires")
    }
    return ManifestComponent(
        component=kind,
        name=name,
        version=version,
        revision=revision,
        source=source,
        license=license_value,
        license_note=license_note,
        files=files,
        requires=tuple(item.strip() for item in requires_raw),
        extra=extra,
    )


def _file(raw: Any, component_name: str) -> ManifestFile:
    if not isinstance(raw, dict):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Every file of {component_name} must be an object.")
    try:
        path = normalize_relative_path(str(raw.get("path", "")))
    except Exception as exc:  # PathRejectedError carries its own safe message
        raise VoiceError(
            ErrorCode.VOICE_MANIFEST_INVALID,
            f"Component {component_name} lists an unsafe file path.",
        ) from exc

    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_FILE_BYTES:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"File {path} has an invalid size.")

    hash_type = str(raw.get("hash_type", "")).strip()
    if hash_type not in HASH_TYPES:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"File {path} has an unknown hash type.")
    digest = str(raw.get("hash", "")).strip().lower()
    expected_length = HASH_TYPES[hash_type]
    if expected_length == 0:
        if digest:
            raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"File {path} declares no hash type but carries a hash.")
    elif len(digest) != expected_length or not _HEX_RE.match(digest):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"File {path} has a malformed {hash_type} value.")

    url = raw.get("url")
    if url is not None:
        url = str(url).strip()
        if not url.startswith("https://") or len(url) > 1024 or any(ord(ch) < 33 for ch in url):
            raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"File {path} has an invalid download URL.")

    return ManifestFile(
        path=path,
        size=size,
        hash_type=hash_type,
        hash=digest,
        url=url,
        hash_source=str(raw.get("hash_source", "")).strip()[:200],
    )


def _text(raw: Mapping[str, Any], key: str, minimum: int, maximum: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Manifest field {key!r} must be a string.")
    value = value.strip()
    if not (minimum <= len(value) <= maximum):
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, f"Manifest field {key!r} has an invalid length.")
    return value


# -- hashing ----------------------------------------------------------------
def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha1_of(path: Path) -> str:
    """The Git object id of a file's content, as Hugging Face publishes it."""
    path = Path(path)
    size = path.stat().st_size
    digest = hashlib.sha1(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_hash(path: Path, hash_type: str) -> Optional[str]:
    if hash_type in ("publisher-sha256", "computed-sha256-at-manifest-time"):
        return sha256_of(path)
    if hash_type == "publisher-git-blob-sha1":
        return git_blob_sha1_of(path)
    return None


# -- verification -----------------------------------------------------------
@dataclass(frozen=True)
class FileCheck:
    path: str
    state: str  # verified | mismatch | missing | size_mismatch | unverifiable
    hash_type: str
    detail: str = ""


@dataclass(frozen=True)
class VerificationReport:
    component: str
    root: Path
    checks: tuple[FileCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.state in ("verified", "unverifiable") for check in self.checks) and any(
            check.state == "verified" for check in self.checks
        )

    @property
    def failures(self) -> tuple[FileCheck, ...]:
        return tuple(check for check in self.checks if check.state not in ("verified", "unverifiable"))

    def describe(self) -> dict:
        counts: dict[str, int] = {}
        for check in self.checks:
            counts[check.state] = counts.get(check.state, 0) + 1
        return {
            "component": self.component,
            "ok": self.ok,
            "files": len(self.checks),
            "states": counts,
            "failures": [{"path": c.path, "state": c.state, "hash_type": c.hash_type} for c in self.failures][:20],
        }


def verify_component(component: ManifestComponent, root: Path) -> VerificationReport:
    """Check every listed file under ``root`` against the manifest, byte for byte."""
    root = Path(root)
    checks: list[FileCheck] = []
    for entry in component.files:
        target = root.joinpath(*entry.path.split("/"))
        if not target.is_file():
            checks.append(FileCheck(entry.path, "missing", entry.hash_type))
            continue
        actual_size = target.stat().st_size
        if actual_size != entry.size:
            checks.append(FileCheck(entry.path, "size_mismatch", entry.hash_type, f"{actual_size} bytes on disk"))
            continue
        if not entry.verifiable:
            checks.append(FileCheck(entry.path, "unverifiable", entry.hash_type, "no publisher hash"))
            continue
        actual = compute_hash(target, entry.hash_type)
        if actual == entry.hash:
            checks.append(FileCheck(entry.path, "verified", entry.hash_type))
        else:
            checks.append(FileCheck(entry.path, "mismatch", entry.hash_type))
    return VerificationReport(component=component.name, root=root, checks=tuple(checks))


def require_verified(report: VerificationReport) -> None:
    if report.ok:
        return
    failures = report.failures
    missing = [c.path for c in failures if c.state == "missing"]
    if failures and len(missing) == len(failures):
        raise VoiceError(
            ErrorCode.VOICE_MODEL_MISSING,
            f"{len(missing)} model file(s) are missing; run the installer.",
            detail={"missing": missing[:10]},
        )
    if not report.checks:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "The manifest lists no files to verify.")
    if not failures:
        raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "No file in the manifest carries a checkable hash.")
    raise VoiceError(
        ErrorCode.VOICE_HASH_MISMATCH,
        "Installed model files do not match the approved manifest; refusing to use them.",
        detail={"failures": [{"path": c.path, "state": c.state} for c in failures][:10]},
    )
