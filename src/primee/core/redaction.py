"""Redaction of sensitive values.

Primee must never place credentials or raw personal identifiers into logs,
audit events, error messages or reports.  This module is intentionally
aggressive: it prefers over-redacting to leaking.

The rules here are heuristics, not a guarantee.  The primary defence is that
Primee never *collects* secrets in the first place; redaction is the second
layer.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
REDACTED_EMAIL = "[REDACTED_EMAIL]"
REDACTED_NUMBER = "[REDACTED_NUMBER]"
REDACTED_PATH = "[REDACTED_USER]"

#: Mapping keys whose *value* is always removed, whatever it looks like.
SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "api-key",
    "authorization",
    "auth_header",
    "cookie",
    "session_id",
    "sessionid",
    "private_key",
    "privatekey",
    "seed_phrase",
    "seedphrase",
    "mnemonic",
    "recovery_code",
    "recoverycode",
    "pin",
    "card_number",
    "cardnumber",
    "cvv",
    "iban",
    "credential",
    "credentials",
    "access_key",
    "refresh_token",
    "client_secret",
)

_KEYWORD = (
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|authorization|"
    r"bearer|cookie|session|private[_-]?key|seed[_-]?phrase|mnemonic|"
    r"recovery[_-]?code|pin|cvv|client[_-]?secret|access[_-]?key|credential)"
)

# Order matters: the most specific patterns run first, so that a broad
# "key = value" rule cannot consume half of a token and leave the rest visible.
_PATTERNS: tuple[tuple[re.Pattern[str], object], ...] = (
    # PEM / OpenSSH private key blocks.
    (
        re.compile(
            r"-----BEGIN[^-]{0,64}PRIVATE KEY-----.*?-----END[^-]{0,64}PRIVATE KEY-----",
            re.IGNORECASE | re.DOTALL,
        ),
        REDACTED,
    ),
    # HTTP style bearer tokens, before the generic key/value rule.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer " + REDACTED),
    # Basic auth inside URLs.
    (re.compile(r"://[^/\s:@]{1,64}:[^/\s@]{1,256}@"), "://" + REDACTED + "@"),
    # key = value / key: value pairs. The optional prefix group catches
    # compound names such as refresh_token, x-api-key and client_secret.
    (
        re.compile(
            rf"(?i)(?<![A-Za-z0-9])((?:[a-z][a-z0-9]*[_-]){{0,2}}{_KEYWORD}s?)"
            rf"(?![A-Za-z0-9])(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;)\]}}]+)"
        ),
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}",
    ),
    # Email addresses (treated as personal data, not secrets).
    (re.compile(r"\b[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+\b"), REDACTED_EMAIL),
    # Long digit runs: card numbers, national IDs, account numbers.
    (re.compile(r"\b\d{12,24}\b"), REDACTED_NUMBER),
    # Windows / POSIX home directories reveal the account name.
    (re.compile(r"(?i)([A-Z]:\\Users\\)[^\\\r\n]+"), lambda m: m.group(1) + REDACTED_PATH),
    (re.compile(r"(/home/|/Users/)[^/\s]+"), lambda m: m.group(1) + REDACTED_PATH),
    # High entropy looking blobs.
    (
        re.compile(
            r"\b(?=[A-Za-z0-9_\-]{40,})(?=[^\s]*\d)(?=[^\s]*[A-Za-z])[A-Za-z0-9_\-]{40,}\b"
        ),
        REDACTED,
    ),
)

_MAX_TEXT = 8000


def redact_text(value: str) -> str:
    """Return ``value`` with anything that looks sensitive replaced."""
    if not isinstance(value, str):
        return value
    text = value
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT] + "…[TRUNCATED]"
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def is_sensitive_key(key: str) -> bool:
    lowered = str(key).strip().lower().replace(" ", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_structure(value: Any, _depth: int = 0) -> Any:
    """Recursively redact a JSON-like structure.

    Values under a sensitive key are removed entirely; every string is passed
    through :func:`redact_text`.
    """
    if _depth > 12:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            safe_key = str(key)
            if is_sensitive_key(safe_key):
                out[safe_key] = REDACTED
            else:
                out[safe_key] = redact_structure(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_structure(item, _depth + 1) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(str(value))
