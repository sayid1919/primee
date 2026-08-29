"""Primee Memory.

A plain Markdown Vault is the authoritative memory. Everything in this package
reads and writes ordinary ``.md`` files under one configured root, and every
cache is rebuilt from those files. There is no database, no vector store and no
hosted memory service: delete the caches and nothing is lost.
"""

from .changelog import ChangeEvent  # noqa: F401
from .links import LinkGraph, WikiLink, extract_links, normalize_target  # noqa: F401
from .operations import (  # noqa: F401
    OPERATION_NAMES,
    OPERATIONS,
    describe_operations,
    is_mutating,
    permission_for,
)
from .page import Page, parse_page, render_page  # noqa: F401
from .schema import (  # noqa: F401
    CONTENT_FOLDERS,
    PAGE_TYPES,
    ROOT_FILES,
    SCHEMA_VERSION,
    PageMetadata,
    SchemaError,
    build_metadata,
)
from .service import MemoryResult, MemoryService, slugify  # noqa: F401

__all__ = [
    "ChangeEvent",
    "CONTENT_FOLDERS",
    "LinkGraph",
    "MemoryResult",
    "MemoryService",
    "OPERATIONS",
    "OPERATION_NAMES",
    "PAGE_TYPES",
    "Page",
    "PageMetadata",
    "ROOT_FILES",
    "SCHEMA_VERSION",
    "SchemaError",
    "WikiLink",
    "build_metadata",
    "describe_operations",
    "extract_links",
    "is_mutating",
    "normalize_target",
    "parse_page",
    "permission_for",
    "render_page",
    "slugify",
]
