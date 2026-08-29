"""Transparent, deterministic routing.

Step One deliberately contains **no** language model.  Routing is a scoring
function over the trigger and exclusion phrases declared in each ``SKILL.md``,
and every decision comes with the exact numbers that produced it.

The router refuses to guess.  When the best match is weak, or when two skills
are too close together, it returns the candidates and an explanation instead of
picking one.

``Router`` is an abstract seam: a future native Primee intelligence engine can
implement the same interface and be swapped in without touching the skill
system, the permission layer or the Vault.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from .errors import ErrorCode
from .skill_loader import SkillRegistry

MATCHED = "matched"
AMBIGUOUS = "ambiguous"
NO_MATCH = "no_match"
UNKNOWN_COMMAND = "unknown_command"
EMPTY = "empty"

DEFAULT_MIN_SCORE = 0.35
DEFAULT_AMBIGUITY_MARGIN = 0.15
DEFAULT_EXCLUSION_THRESHOLD = 0.5
MAX_REQUEST_LENGTH = 2000

_COMMAND_RE = re.compile(r"^[/!]([A-Za-z][A-Za-z0-9_]{0,31})\b")
_PUNCTUATION = dict.fromkeys(
    ord(char) for char in "\"'`.,;:!?()[]{}<>-–—_/\\|@#$%^&*+=~«»،؛؟…"
)


def normalize(text: str) -> str:
    """Casefold, strip punctuation and collapse whitespace.

    Works for Persian and English alike: no language specific tokenisation is
    used, only Unicode normalisation and whitespace splitting.
    """
    if not isinstance(text, str):
        return ""
    value = unicodedata.normalize("NFKC", text).casefold()
    value = value.translate(_PUNCTUATION)
    value = value.replace("‌", " ").replace("‍", " ")
    return " ".join(value.split())


def tokens(text: str) -> list[str]:
    normalized = normalize(text)
    return normalized.split() if normalized else []


def phrase_score(request_normalized: str, request_tokens: set[str], phrase: str) -> float:
    """Score a single trigger or exclusion phrase against the request."""
    phrase_normalized = normalize(phrase)
    if not phrase_normalized:
        return 0.0
    phrase_tokens = phrase_normalized.split()

    if request_normalized == phrase_normalized:
        return 1.0
    if f" {phrase_normalized} " in f" {request_normalized} ":
        return min(1.0, 0.70 + 0.05 * min(len(phrase_tokens), 5))
    if all(token in request_tokens for token in phrase_tokens):
        return 0.55 if len(phrase_tokens) > 1 else 0.50
    overlap = sum(1 for token in phrase_tokens if token in request_tokens)
    ratio = overlap / len(phrase_tokens)
    if ratio >= 0.5:
        return round(0.40 * ratio, 4)
    return 0.0


@dataclass
class SkillScore:
    skill_name: str
    score: float
    matched_triggers: list[tuple[str, float]] = field(default_factory=list)
    excluded_by: list[tuple[str, float]] = field(default_factory=list)
    vetoed: bool = False

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "score": round(self.score, 4),
            "matched_triggers": [
                {"phrase": phrase, "score": round(value, 4)}
                for phrase, value in self.matched_triggers
            ],
            "excluded_by": [
                {"phrase": phrase, "score": round(value, 4)}
                for phrase, value in self.excluded_by
            ],
            "vetoed": self.vetoed,
        }


@dataclass
class RouteDecision:
    status: str
    request: str
    skill_name: Optional[str] = None
    candidates: list[SkillScore] = field(default_factory=list)
    all_scores: list[SkillScore] = field(default_factory=list)
    explanation: str = ""
    error_code: Optional[str] = None
    method: str = "deterministic"

    @property
    def matched(self) -> bool:
        return self.status == MATCHED

    @property
    def needs_clarification(self) -> bool:
        return self.status in (AMBIGUOUS, NO_MATCH, UNKNOWN_COMMAND, EMPTY)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "skill_name": self.skill_name,
            "explanation": self.explanation,
            "error_code": self.error_code,
            "method": self.method,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "all_scores": [score.to_dict() for score in self.all_scores],
        }


class Router:
    """Interface for every Primee routing engine."""

    def route(self, request: str, registry: SkillRegistry) -> RouteDecision:  # pragma: no cover
        raise NotImplementedError


class DeterministicRouter(Router):
    """The Step One router: explicit commands, trigger scoring, exclusions."""

    def __init__(
        self,
        *,
        min_score: float = DEFAULT_MIN_SCORE,
        ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
        exclusion_threshold: float = DEFAULT_EXCLUSION_THRESHOLD,
    ) -> None:
        self.min_score = min_score
        self.ambiguity_margin = ambiguity_margin
        self.exclusion_threshold = exclusion_threshold

    def route(self, request: str, registry: SkillRegistry) -> RouteDecision:
        raw = (request or "").strip()
        if len(raw) > MAX_REQUEST_LENGTH:
            raw = raw[:MAX_REQUEST_LENGTH]
        if not raw:
            return RouteDecision(
                status=EMPTY,
                request=raw,
                explanation="The request was empty, so there was nothing to route.",
                error_code=ErrorCode.EMPTY_REQUEST,
            )

        command = _COMMAND_RE.match(raw)
        if command:
            name = command.group(1).lower()
            if registry.get(name) is not None:
                return RouteDecision(
                    status=MATCHED,
                    request=raw,
                    skill_name=name,
                    explanation=f"Explicit command '/{name}' selected the skill directly.",
                    candidates=[SkillScore(skill_name=name, score=1.0)],
                    all_scores=[SkillScore(skill_name=name, score=1.0)],
                )
            return RouteDecision(
                status=UNKNOWN_COMMAND,
                request=raw,
                explanation=(
                    f"There is no installed skill named '{name}'. "
                    f"Installed skills: {', '.join(registry.names()) or 'none'}."
                ),
                error_code=ErrorCode.UNKNOWN_COMMAND,
            )

        request_normalized = normalize(raw)
        request_tokens = set(request_normalized.split())

        scores: list[SkillScore] = []
        for skill in registry:
            manifest = skill.manifest
            entry = SkillScore(skill_name=manifest.name, score=0.0)

            for phrase in manifest.exclusions:
                value = phrase_score(request_normalized, request_tokens, phrase)
                if value >= self.exclusion_threshold:
                    entry.excluded_by.append((phrase, value))

            best = 0.0
            for phrase in manifest.triggers:
                value = phrase_score(request_normalized, request_tokens, phrase)
                if value > 0.0:
                    entry.matched_triggers.append((phrase, value))
                    best = max(best, value)

            entry.matched_triggers.sort(key=lambda item: (-item[1], item[0]))
            if entry.excluded_by:
                entry.vetoed = True
                entry.score = 0.0
            else:
                extra = max(0, len(entry.matched_triggers) - 1)
                entry.score = min(0.99, best + 0.05 * extra) if best < 1.0 else 1.0
            scores.append(entry)

        scores.sort(key=lambda item: (-item.score, item.skill_name))
        candidates = [item for item in scores if item.score >= self.min_score]

        if not candidates:
            vetoed = [item.skill_name for item in scores if item.vetoed]
            explanation = (
                "No installed skill matched this request with enough confidence "
                f"(minimum score {self.min_score})."
            )
            if vetoed:
                explanation += (
                    " These skills matched a trigger but were vetoed by an "
                    f"exclusion phrase: {', '.join(sorted(vetoed))}."
                )
            return RouteDecision(
                status=NO_MATCH,
                request=raw,
                candidates=[],
                all_scores=scores,
                explanation=explanation,
                error_code=ErrorCode.NO_MATCHING_SKILL,
            )

        if len(candidates) > 1:
            gap = candidates[0].score - candidates[1].score
            if gap < self.ambiguity_margin:
                tied = [
                    item
                    for item in candidates
                    if candidates[0].score - item.score < self.ambiguity_margin
                ]
                names = ", ".join(item.skill_name for item in tied)
                return RouteDecision(
                    status=AMBIGUOUS,
                    request=raw,
                    candidates=tied,
                    all_scores=scores,
                    explanation=(
                        f"This request matches more than one skill ({names}) with "
                        f"scores that are within {self.ambiguity_margin} of each "
                        "other. Primee will not guess - please say which skill you "
                        "meant, for example by typing '/" + tied[0].skill_name + "'."
                    ),
                    error_code=ErrorCode.AMBIGUOUS_REQUEST,
                )

        winner = candidates[0]
        phrases = ", ".join(f"'{p}' ({v:.2f})" for p, v in winner.matched_triggers[:3])
        return RouteDecision(
            status=MATCHED,
            request=raw,
            skill_name=winner.skill_name,
            candidates=candidates,
            all_scores=scores,
            explanation=(
                f"Skill '{winner.skill_name}' scored {winner.score:.2f} on trigger "
                f"phrase(s) {phrases}, ahead of every other skill by at least "
                f"{self.ambiguity_margin}."
            ),
        )
