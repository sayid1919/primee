# Routing

Step One routing is deterministic and fully inspectable. **There is no language
model anywhere in this path.** The same request always produces the same
decision, and every decision comes with the numbers that produced it.

## The algorithm

1. **Empty request** → status `empty`.
2. **Explicit command.** If the request starts with `/name` or `!name`, that
   skill is selected directly. If no such skill exists, status is
   `unknown_command` — Primee does not fall back to guessing.
3. **Normalisation.** The request is NFKC-normalised, case-folded, stripped of
   punctuation (ASCII and Persian), and collapsed to single spaces. Persian and
   English are handled identically; there is no language-specific tokenisation.
4. **Exclusions first.** Each exclusion phrase is scored against the request. A
   score of `0.5` or more vetoes the skill outright: its score becomes `0` and it
   cannot win, no matter how well its triggers matched.
5. **Triggers.** Each trigger phrase is scored:

   | Situation | Score |
   | --- | --- |
   | request equals the trigger exactly | `1.00` |
   | the trigger appears as a whole phrase in the request | `0.75` – `1.00`, longer phrases score higher |
   | every word of a multi-word trigger appears somewhere | `0.55` |
   | a single-word trigger appears as a word | `0.50` |
   | at least half the trigger's words appear | `0.40 × ratio` |
   | otherwise | `0.00` |

   The skill's score is its best trigger score, plus `0.05` for each additional
   trigger that matched at all, capped at `0.99` (only an exact match reaches
   `1.00`).
6. **Threshold.** Skills scoring below `min_route_score` (default `0.35`) are
   dropped. If none remain, status is `no_match`, and the explanation names any
   skill that a trigger matched but an exclusion vetoed.
7. **Ambiguity.** If the top two surviving scores are within
   `ambiguity_margin` (default `0.15`), status is `ambiguous`. Primee returns
   every close candidate, explains why, and suggests `/skillname`.
8. Otherwise the single winner is returned with its matched phrases and scores.

Ties are never broken by picking one. Sorting is `(-score, name)` purely so the
*candidate list* is reproducible.

## Seeing a decision

```
python run_primee.py explain "what changed since yesterday"
```

```
Request : what changed since yesterday
Status  : matched
Skill   : trends
Why     : Skill 'trends' scored 0.85 on trigger phrase(s) 'what changed' (0.85), ...
Scores  :
  trends     0.85  what changed=0.85
  inbox      0.00 (vetoed by exclusion)
  metrics    0.00 (vetoed by exclusion)
  plan       0.00 (vetoed by exclusion)
  vault      0.00
```

Add `--json` for the full breakdown, including every phrase that matched and
every exclusion that fired.

## Tuning

```toml
[runtime]
min_route_score  = 0.35   # raise it to make Primee ask more often
ambiguity_margin = 0.15   # raise it to make Primee refuse to guess more often
```

## Replacing the router later

`Router` is an abstract class with a single method:

```python
class Router:
    def route(self, request: str, registry: SkillRegistry) -> RouteDecision: ...
```

A future native Primee intelligence engine implements it and is passed to
`PrimeeRuntime(router=...)`. The skill system, permission layer, Vault and audit
trail are untouched by that change. `RouteDecision.method` records which engine
made each decision, so the audit log stays meaningful across the transition.
